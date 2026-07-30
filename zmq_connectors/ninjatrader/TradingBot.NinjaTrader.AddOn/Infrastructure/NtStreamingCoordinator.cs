using System;
using System.Collections.Generic;
using System.Threading;
using System.Windows.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// NinjaTrader-specific streaming coordinator.
    /// Encapsulates all NinjaTrader market-data and BarsRequest usage.
    /// Supports streaming multiple instruments simultaneously, one
    /// <see cref="InstrumentStream"/> context per subscribed instrument.
    /// </summary>
    public sealed class NtStreamingCoordinator : IStreamingCoordinator
    {
        private sealed class InstrumentStream
        {
            public string FullName;
            public string Pair;
            public Instrument Instrument;
            public BarsRequest LiveBarsRequest;
            public readonly BarStreamTracker BarTracker = new BarStreamTracker();
            public SessionIterator SessionIterator;
            public System.Timers.Timer LiveBarsDelayTimer;
            public bool LiveBarsSubscribed;
            public bool MarketIsOpen = true;
            public DateTime LastMarketStatusSent = DateTime.MinValue;
        }

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly ZmqConfiguration _config;
        private readonly TickRateLimiter _tickRateLimiter;
        private readonly TickRateLimiter _partialBarRateLimiter;

        private readonly Dictionary<string, InstrumentStream> _streams =
            new Dictionary<string, InstrumentStream>(StringComparer.OrdinalIgnoreCase);
        private readonly List<string> _subscriptionOrder = new List<string>();
        private readonly object _streamsLock = new object();
        private System.Timers.Timer _barsRequestWatchdog;

        private long _ticksSent;
        private long _barsSent;
        private long _partialBarsSent;
        private readonly object _barSendLock = new object();

        public NtStreamingCoordinator(IZmqNetwork network, ILogger logger, ZmqConfiguration config)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _tickRateLimiter = new TickRateLimiter(config.MaxTicksPerSecond);
            _partialBarRateLimiter = new TickRateLimiter(1);
        }

        public string CurrentInstrument
        {
            get
            {
                lock (_streamsLock)
                    return _subscriptionOrder.Count > 0 ? _subscriptionOrder[0] : string.Empty;
            }
        }

        public bool IsStreaming
        {
            get
            {
                lock (_streamsLock)
                    return _streams.Count > 0;
            }
        }

        public IReadOnlyList<string> SubscribedInstruments
        {
            get
            {
                lock (_streamsLock)
                    return _subscriptionOrder.ToArray();
            }
        }

        public (long ticks, long bars, long partials) GetStats() =>
            (_ticksSent, _barsSent, _partialBarsSent);

        public bool Start(string instrument)
        {
            if (Dispatcher.CurrentDispatcher.CheckAccess())
                return StartCore(instrument);
            return (bool)Dispatcher.CurrentDispatcher.Invoke(new Func<bool>(() => StartCore(instrument)));
        }

        private bool StartCore(string instrument)
        {
            if (string.IsNullOrWhiteSpace(instrument))
            {
                _logger.Error("Subscribe command received with empty instrument");
                return false;
            }

            var resolved = Instrument.GetInstrument(instrument);
            if (resolved == null)
            {
                _logger.Error($"Instrument '{instrument}' not found");
                return false;
            }

            InstrumentStream ctx;
            lock (_streamsLock)
            {
                if (_streams.ContainsKey(instrument))
                {
                    _logger.Info($"Already subscribed to instrument {instrument}");
                    return true;
                }

                ctx = new InstrumentStream
                {
                    FullName = instrument,
                    Pair = instrument.Split(' ')[0],
                    Instrument = resolved
                };
                _streams[instrument] = ctx;
                _subscriptionOrder.Add(instrument);
            }

            // NinjaTrader API calls must run OUTSIDE _streamsLock (same pattern as
            // StopCore): an NT call can block (provider handshake, data-thread sync),
            // and holding the lock here would wedge every bar path, watchdog, and
            // command that needs FindContext/_streamsLock.
            ctx.Instrument.MarketData.Update += OnMarketDataUpdate;
            _logger.Info($"Subscribed to market data for {instrument}");
            StartLiveBarsDelayTimer(ctx);

            // Race: a concurrent Stop()/StopCore() may have removed this context
            // while the NT calls above were running. Undo the NT subscription if so.
            bool stillRegistered;
            lock (_streamsLock)
            {
                stillRegistered = _streams.TryGetValue(instrument, out var current) &&
                                  ReferenceEquals(current, ctx);
            }
            if (!stillRegistered)
            {
                _logger.Warning($"Subscription for {instrument} was stopped during subscribe, rolling back NT subscription");
                ctx.Instrument.MarketData.Update -= OnMarketDataUpdate;
                StopLiveBarsDelayTimer(ctx);
                return false;
            }

            _logger.Success($"Subscribed to instrument {instrument}");
            return true;
        }

        public bool Stop(string instrument)
        {
            if (Dispatcher.CurrentDispatcher.CheckAccess())
                return StopCore(instrument);
            return (bool)Dispatcher.CurrentDispatcher.Invoke(new Func<bool>(() => StopCore(instrument)));
        }

        private bool StopCore(string instrument)
        {
            if (string.IsNullOrWhiteSpace(instrument))
            {
                _logger.Error("Unsubscribe command received with empty instrument");
                return false;
            }

            InstrumentStream ctx;
            lock (_streamsLock)
            {
                if (!_streams.TryGetValue(instrument, out ctx))
                {
                    _logger.Info($"Not subscribed to instrument {instrument}");
                    return false;
                }
                _streams.Remove(instrument);
                _subscriptionOrder.Remove(instrument);
            }

            // NinjaTrader API calls must run OUTSIDE _streamsLock (same pattern as
            // StartCore/StopCore()): an NT call can block, and holding the lock
            // would wedge every bar path, watchdog, and command that needs
            // FindContext/_streamsLock. Other instruments are untouched.
            UnsubscribeFromLiveBars(ctx);
            if (ctx.Instrument != null)
            {
                ctx.Instrument.MarketData.Update -= OnMarketDataUpdate;
                _logger.Info($"Unsubscribed from market data for {ctx.FullName ?? "<none>"}");
            }

            bool anyLeft;
            lock (_streamsLock)
                anyLeft = _streams.Count > 0;
            if (!anyLeft)
                StopBarsRequestWatchdog();

            _logger.Success($"Unsubscribed from instrument {instrument}");
            return true;
        }

        public void Stop()
        {
            if (Dispatcher.CurrentDispatcher.CheckAccess())
            {
                StopCore();
                return;
            }
            Dispatcher.CurrentDispatcher.Invoke(new Action(StopCore));
        }

        private void StopCore()
        {
            List<InstrumentStream> snapshot;
            lock (_streamsLock)
            {
                snapshot = new List<InstrumentStream>(_streams.Values);
                _streams.Clear();
                _subscriptionOrder.Clear();
            }

            StopBarsRequestWatchdog();

            foreach (var ctx in snapshot)
            {
                UnsubscribeFromLiveBars(ctx);
                if (ctx.Instrument != null)
                {
                    ctx.Instrument.MarketData.Update -= OnMarketDataUpdate;
                    _logger.Info($"Unsubscribed from market data for {ctx.FullName ?? "<none>"}");
                }
            }
        }

        private InstrumentStream FindContext(Instrument instrument)
        {
            if (instrument == null) return null;
            lock (_streamsLock)
            {
                foreach (var ctx in _streams.Values)
                {
                    if (ReferenceEquals(ctx.Instrument, instrument))
                        return ctx;
                }
                var fullName = instrument.FullName;
                foreach (var ctx in _streams.Values)
                {
                    if (string.Equals(ctx.Instrument.FullName, fullName, StringComparison.OrdinalIgnoreCase))
                        return ctx;
                }
                var masterName = instrument.MasterInstrument?.Name;
                if (masterName != null)
                {
                    foreach (var ctx in _streams.Values)
                    {
                        if (string.Equals(ctx.Instrument.MasterInstrument.Name, masterName, StringComparison.OrdinalIgnoreCase))
                            return ctx;
                    }
                }
                return null;
            }
        }

        private void OnMarketDataUpdate(object sender, MarketDataEventArgs e)
        {
            try
            {
                if (e.MarketDataType != MarketDataType.Last)
                    return;

                var ctx = FindContext(e.Instrument);
                if (ctx == null)
                    return;

                if (!ctx.LiveBarsSubscribed)
                {
                    StopLiveBarsDelayTimer(ctx);
                    SubscribeToLiveBars(ctx);
                }

                if (!_tickRateLimiter.TryAllow())
                    return;

                _network?.SendTick(
                    e.Instrument.MasterInstrument.Name,
                    e.Price,
                    (long)e.Volume,
                    e.Time);

                _ticksSent++;
            }
            catch (Exception ex)
            {
                _logger.Error("Market data error", ex);
                _network?.SendError("ninjatrader", "market_data_error", ex.Message);
            }
        }

        private void SubscribeToLiveBars(InstrumentStream ctx)
        {
            if (ctx.Instrument == null)
            {
                _logger.Error($"Cannot subscribe to live bars for {ctx.FullName}, instrument is null");
                return;
            }
            if (ctx.LiveBarsSubscribed)
                return;

            ctx.LiveBarsSubscribed = true;

            ctx.LiveBarsRequest = new BarsRequest(ctx.Instrument, 2)
            {
                BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                TradingHours = ctx.Instrument.MasterInstrument.TradingHours
            };
            ctx.LiveBarsRequest.Update += (s, e) => OnLiveBarsUpdate(ctx, s, e);
            ctx.LiveBarsRequest.Request((bars, errorCode, errorMessage) =>
            {
                try
                {
                    if (errorCode != ErrorCode.NoError)
                    {
                        _logger.Error($"Live bars request failed for {ctx.FullName}: {errorMessage}");
                        ctx.LiveBarsSubscribed = false;
                        return;
                    }
                    if (bars?.Bars != null && bars.Bars.Count > 0)
                    {
                        lock (_barSendLock)
                        {
                            var pair = ctx.Pair;
                            for (int i = 0; i < bars.Bars.Count - 1; i++)
                            {
                                _network?.SendBar(pair,
                                    bars.Bars.GetTime(i),
                                    bars.Bars.GetOpen(i),
                                    bars.Bars.GetHigh(i),
                                    bars.Bars.GetLow(i),
                                    bars.Bars.GetClose(i),
                                    (long)bars.Bars.GetVolume(i),
                                    isPartial: false);
                            }

                            int lastCompletedIdx = Math.Max(0, bars.Bars.Count - 2);
                            ctx.BarTracker.Reset(lastCompletedIdx);
                        }
                        ctx.SessionIterator = new SessionIterator(ctx.Instrument.MasterInstrument.TradingHours);
                        _logger.Info($"Live bars stream ready for {ctx.FullName}. Cached {bars.Bars.Count} bars.");
                    }
                }
                catch (Exception callbackEx)
                {
                    _logger.Error("Live bars request callback error", callbackEx);
                    ctx.LiveBarsSubscribed = false;
                }
            });

            _logger.Info($"Subscribed to live 1m bars for {ctx.FullName}");
            StartBarsRequestWatchdog();
        }

        private void UnsubscribeFromLiveBars(InstrumentStream ctx)
        {
            StopLiveBarsDelayTimer(ctx);
            if (ctx.LiveBarsRequest != null)
            {
                ctx.LiveBarsRequest.Dispose();
                ctx.LiveBarsRequest = null;
            }
            ctx.LiveBarsSubscribed = false;
            ctx.BarTracker.Reset(-1);
        }

        private void OnLiveBarsUpdate(InstrumentStream ctx, object sender, BarsUpdateEventArgs e)
        {
            try
            {
                if (!IsStreaming)
                    return;

                var series = e.BarsSeries;
                if (series == null || series.Count == 0)
                    return;

                var formingBarTime = series.GetTime(series.Count - 1);
                var pair = ctx.Pair;

                lock (_barSendLock)
                {
                    int sent = 0;
                    foreach (var bar in ctx.BarTracker.GetUnsentBars(series))
                    {
                        _network?.SendBar(pair, bar.Time, bar.Open, bar.High, bar.Low, bar.Close, bar.Volume,
                            isPartial: false, seqNum: bar.SequenceNumber);
                        _barsSent++;
                        ctx.BarTracker.MarkSent(bar.Index, formingBarTime);
                        sent++;
                    }
                    if (sent > 1)
                        _logger.Info($"[CatchUp] {pair} sent={sent} forming={formingBarTime:HH:mm:ss} lastIdx={ctx.BarTracker.LastSentIndex} seriesCount={series.Count}");
                }

                if (_partialBarRateLimiter?.TryAllow() == true)
                {
                    int formingIdx = series.Count - 1;
                    _network?.SendBar(pair, formingBarTime,
                        series.GetOpen(formingIdx), series.GetHigh(formingIdx),
                        series.GetLow(formingIdx), series.GetClose(formingIdx),
                        (long)series.GetVolume(formingIdx), isPartial: true);
                    _partialBarsSent++;
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Live bars update error", ex);
                _network?.SendError("ninjatrader", "live_bar_error", ex.Message);
            }
        }

        private void StartLiveBarsDelayTimer(InstrumentStream ctx)
        {
            StopLiveBarsDelayTimer(ctx);
            ctx.LiveBarsDelayTimer = new System.Timers.Timer(10000);
            ctx.LiveBarsDelayTimer.Elapsed += (s, e) =>
            {
                StopLiveBarsDelayTimer(ctx);
                if (!ctx.LiveBarsSubscribed && IsStreaming)
                {
                    _logger.Info($"[LiveBars] No tick received within 10s for {ctx.FullName}, creating BarsRequest anyway");
                    SubscribeToLiveBars(ctx);
                }
            };
            ctx.LiveBarsDelayTimer.AutoReset = false;
            ctx.LiveBarsDelayTimer.Start();
        }

        private void StopLiveBarsDelayTimer(InstrumentStream ctx)
        {
            if (ctx.LiveBarsDelayTimer != null)
            {
                ctx.LiveBarsDelayTimer.Stop();
                ctx.LiveBarsDelayTimer.Dispose();
                ctx.LiveBarsDelayTimer = null;
            }
        }

        private void StartBarsRequestWatchdog()
        {
            if (_barsRequestWatchdog != null)
                return;

            _barsRequestWatchdog = new System.Timers.Timer(30000);
            _barsRequestWatchdog.Elapsed += (s, e) =>
            {
                try
                {
                    List<InstrumentStream> snapshot;
                    lock (_streamsLock)
                    {
                        snapshot = new List<InstrumentStream>(_streams.Values);
                    }

                    foreach (var ctx in snapshot)
                    {
                        if (ctx.LiveBarsRequest == null)
                            continue;

                        CheckMarketStatus(ctx);

                        var lastForming = ctx.BarTracker.LastFormingBarTime;
                        if (lastForming != DateTime.MinValue)
                        {
                            var elapsed = DateTime.Now - lastForming;
                            if (elapsed.TotalSeconds > 75)
                            {
                                _logger.Warning($"[BarsRequestWatchdog] {ctx.FullName}: No forming bar update in {elapsed.TotalSeconds:F0}s. Recreating BarsRequest...");
                                UnsubscribeFromLiveBars(ctx);
                                SubscribeToLiveBars(ctx);
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger.Error("BarsRequest watchdog error", ex);
                }
            };
            _barsRequestWatchdog.AutoReset = true;
            _barsRequestWatchdog.Start();
        }

        private void StopBarsRequestWatchdog()
        {
            if (_barsRequestWatchdog != null)
            {
                _barsRequestWatchdog.Stop();
                _barsRequestWatchdog.Dispose();
                _barsRequestWatchdog = null;
            }
        }

        private void CheckMarketStatus(InstrumentStream ctx)
        {
            if (ctx.SessionIterator == null) return;

            DateTime nextBegin;
            bool isOpen;
            try
            {
                isOpen = ctx.SessionIterator.IsInSession(DateTime.Now, false, true);
                ctx.SessionIterator.GetNextSession(DateTime.Now, false);
                nextBegin = ctx.SessionIterator.ActualSessionBegin;
            }
            catch (Exception ex)
            {
                _logger.Warning($"[MarketStatus] Failed to query session for {ctx.FullName}: {ex.Message}");
                return;
            }

            bool shouldSend = isOpen != ctx.MarketIsOpen || (DateTime.Now - ctx.LastMarketStatusSent).TotalMinutes > 5;
            if (shouldSend)
            {
                ctx.MarketIsOpen = isOpen;
                ctx.LastMarketStatusSent = DateTime.Now;
                var pair = ctx.Pair;
                _network?.SendMarketStatus(ctx.MarketIsOpen, nextBegin, pair);
                var status = ctx.MarketIsOpen ? "OPEN" : "CLOSED";
                _logger.Info($"[MarketStatus] {pair} market is {status}, next_open={nextBegin:yyyy-MM-dd HH:mm:ss}");
            }
        }
    }
}
