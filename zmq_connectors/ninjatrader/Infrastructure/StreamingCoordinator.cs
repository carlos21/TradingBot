// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Streaming Coordinator
// Owns live market-data subscription and live-bar streaming.
// All NinjaTrader UI-thread-sensitive operations are marshalled here.
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using System.Windows.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Coordinates market-data and live-bar streaming for one instrument.
    /// Encapsulates the UI-thread requirements of NinjaTrader's BarsRequest
    /// and keeps the connector focused on orchestration.
    /// </summary>
    internal sealed class StreamingCoordinator : IStreamingCoordinator
    {
        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly ZmqConfiguration _config;

        // Current state
        private string _currentInstrument;
        private Instrument _subscribedInstrument;
        private bool _isStreaming;

        // Live bar streaming
        private BarsRequest _liveBarsRequest;
        private readonly BarStreamTracker _barTracker = new BarStreamTracker();
        private readonly object _barSendLock = new object();
        private System.Timers.Timer _liveBarsDelayTimer;
        private System.Timers.Timer _barsRequestWatchdog;
        private bool _liveBarsSubscribed;

        // Rate limiters
        private readonly TickRateLimiter _tickRateLimiter;
        private readonly TickRateLimiter _partialBarRateLimiter;

        // Session / market status
        private SessionIterator _sessionIterator;
        private bool _marketIsOpen = true;
        private DateTime _lastMarketStatusSent = DateTime.MinValue;

        // Stats
        private long _ticksSent;
        private long _barsSent;
        private long _partialBarsSent;

        public StreamingCoordinator(ZmqNetwork network, ILogger logger, ZmqConfiguration config)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _tickRateLimiter = new TickRateLimiter(config.MaxTicksPerSecond);
            _partialBarRateLimiter = new TickRateLimiter(1);
        }

        public string CurrentInstrument => _currentInstrument ?? string.Empty;
        public bool IsStreaming => _isStreaming;
        public SessionIterator SessionIterator => _sessionIterator;

        public (long ticks, long bars, long partials) GetStats() =>
            (_ticksSent, _barsSent, _partialBarsSent);

        /// <summary>
        /// Start streaming for the supplied instrument. Marshals to the UI thread.
        /// </summary>
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

            _currentInstrument = instrument;
            _subscribedInstrument = resolved;
            _isStreaming = true;

            SubscribeToMarketData();
            // BarsRequest is created on the first tick (or 10s timer fallback)
            // to ensure NinjaTrader's data connection is fully established.
            StartLiveBarsDelayTimer();

            _logger.Success($"Subscribed to instrument {instrument}");
            return true;
        }

        /// <summary>
        /// Stop streaming and clean up. Marshals to the UI thread.
        /// </summary>
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
            UnsubscribeFromLiveBars();
            UnsubscribeFromMarketData();
            _currentInstrument = null;
            _subscribedInstrument = null;
            _sessionIterator = null;
            _isStreaming = false;
        }

        private string Pair => string.IsNullOrEmpty(_currentInstrument)
            ? string.Empty
            : _currentInstrument.Split(' ')[0];

        // ═══════════════════════════════════════════════════════════════════
        // Market data
        // ═══════════════════════════════════════════════════════════════════

        private void SubscribeToMarketData()
        {
            if (_subscribedInstrument == null)
                return;

            _subscribedInstrument.MarketData.Update += OnMarketDataUpdate;
            _logger.Info($"Subscribed to market data for {_currentInstrument}");
        }

        private void UnsubscribeFromMarketData()
        {
            if (_subscribedInstrument != null)
            {
                _subscribedInstrument.MarketData.Update -= OnMarketDataUpdate;
                _logger.Info($"Unsubscribed from market data for {_currentInstrument ?? "<none>"}");
                _subscribedInstrument = null;
            }
        }

        private void OnMarketDataUpdate(object sender, MarketDataEventArgs e)
        {
            try
            {
                if (!_isStreaming || e.MarketDataType != MarketDataType.Last)
                    return;

                // Create BarsRequest on the first tick after connect.
                if (!_liveBarsSubscribed)
                {
                    StopLiveBarsDelayTimer();
                    SubscribeToLiveBars();
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

        // ═══════════════════════════════════════════════════════════════════
        // Live bars
        // ═══════════════════════════════════════════════════════════════════

        private void SubscribeToLiveBars()
        {
            if (_subscribedInstrument == null)
            {
                _logger.Error("Cannot subscribe to live bars, instrument is null");
                return;
            }
            if (_liveBarsSubscribed)
                return;

            _liveBarsSubscribed = true;

            _liveBarsRequest = new BarsRequest(_subscribedInstrument, 2)
            {
                BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                TradingHours = TradingHours.Get("Default 24 x 7")
            };
            _liveBarsRequest.Update += OnLiveBarsUpdate;
            _liveBarsRequest.Request((bars, errorCode, errorMessage) =>
            {
                try
                {
                    if (errorCode != ErrorCode.NoError)
                    {
                        _logger.Error($"Live bars request failed: {errorMessage}");
                        _liveBarsSubscribed = false;
                        return;
                    }
                    if (bars?.Bars != null && bars.Bars.Count > 0)
                    {
                        lock (_barSendLock)
                        {
                            var pair = Pair;
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
                            _barTracker.Reset(lastCompletedIdx);
                        }
                        _sessionIterator = new SessionIterator(_subscribedInstrument.MasterInstrument.TradingHours);
                        _logger.Info($"Live bars stream ready. Cached {bars.Bars.Count} bars, lastCompletedIdx={_barTracker.LastSentIndex}, sent {Math.Max(0, bars.Bars.Count - 1)} initial bar(s). Market-status session: {_subscribedInstrument.MasterInstrument.TradingHours?.Name ?? "default"}");
                    }
                }
                catch (Exception callbackEx)
                {
                    _logger.Error("Live bars request callback error", callbackEx);
                    _liveBarsSubscribed = false;
                }
            });

            _logger.Info("Subscribed to live 1m bars");
            StartBarsRequestWatchdog();
        }

        private void UnsubscribeFromLiveBars()
        {
            StopLiveBarsDelayTimer();
            StopBarsRequestWatchdog();
            if (_liveBarsRequest != null)
            {
                _liveBarsRequest.Update -= OnLiveBarsUpdate;
                _liveBarsRequest.Dispose();
                _liveBarsRequest = null;
            }
            _liveBarsSubscribed = false;
            _barTracker.Reset(-1);
        }

        private void OnLiveBarsUpdate(object sender, BarsUpdateEventArgs e)
        {
            try
            {
                if (!_isStreaming)
                    return;

                var series = e.BarsSeries;
                if (series == null || series.Count == 0)
                    return;

                var formingBarTime = series.GetTime(series.Count - 1);
                var pair = Pair;

                lock (_barSendLock)
                {
                    int sent = 0;
                    foreach (var bar in _barTracker.GetUnsentBars(series))
                    {
                        _network?.SendBar(pair, bar.Time, bar.Open, bar.High, bar.Low, bar.Close, bar.Volume,
                            isPartial: false, seqNum: bar.SequenceNumber);
                        _barsSent++;
                        _barTracker.MarkSent(bar.Index, formingBarTime);
                        sent++;
                    }
                    if (sent > 1)
                        _logger.Info($"[CatchUp] sent={sent} forming={formingBarTime:HH:mm:ss} lastIdx={_barTracker.LastSentIndex} seriesCount={series.Count}");
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

        private void StartLiveBarsDelayTimer()
        {
            StopLiveBarsDelayTimer();
            _liveBarsDelayTimer = new System.Timers.Timer(10000);
            _liveBarsDelayTimer.Elapsed += (s, e) =>
            {
                StopLiveBarsDelayTimer();
                if (!_liveBarsSubscribed && _isStreaming)
                {
                    _logger.Info("[LiveBars] No tick received within 10s, creating BarsRequest anyway");
                    SubscribeToLiveBars();
                }
            };
            _liveBarsDelayTimer.AutoReset = false;
            _liveBarsDelayTimer.Start();
        }

        private void StopLiveBarsDelayTimer()
        {
            if (_liveBarsDelayTimer != null)
            {
                _liveBarsDelayTimer.Stop();
                _liveBarsDelayTimer.Dispose();
                _liveBarsDelayTimer = null;
            }
        }

        private void StartBarsRequestWatchdog()
        {
            StopBarsRequestWatchdog();
            _barsRequestWatchdog = new System.Timers.Timer(30000);
            _barsRequestWatchdog.Elapsed += (s, e) =>
            {
                try
                {
                    if (!_isStreaming || _liveBarsRequest == null)
                        return;

                    // Notify Python of session changes on every watchdog tick.
                    CheckMarketStatus();

                    var lastForming = _barTracker.LastFormingBarTime;
                    if (lastForming != DateTime.MinValue)
                    {
                        var elapsed = DateTime.Now - lastForming;
                        if (elapsed.TotalSeconds > 75)
                        {
                            _logger.Warning($"[BarsRequestWatchdog] No forming bar update in {elapsed.TotalSeconds:F0}s (threshold=75s). Recreating BarsRequest...");
                            UnsubscribeFromLiveBars();
                            SubscribeToLiveBars();
                            return;
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

        private void CheckMarketStatus()
        {
            if (_sessionIterator == null) return;

            DateTime nextBegin;
            bool isOpen;
            try
            {
                isOpen = _sessionIterator.IsInSession(DateTime.Now, false, true);
                _sessionIterator.GetNextSession(DateTime.Now, false);
                nextBegin = _sessionIterator.ActualSessionBegin;
            }
            catch (Exception ex)
            {
                _logger.Warning($"[MarketStatus] Failed to query session: {ex.Message}");
                return;
            }

            // Send on state change OR at least every 5 minutes as a heartbeat
            bool shouldSend = isOpen != _marketIsOpen || (DateTime.Now - _lastMarketStatusSent).TotalMinutes > 5;

            if (shouldSend)
            {
                _marketIsOpen = isOpen;
                _lastMarketStatusSent = DateTime.Now;
                var pair = Pair;
                _network?.SendMarketStatus(_marketIsOpen, nextBegin, pair);
                var status = _marketIsOpen ? "OPEN" : "CLOSED";
                _logger.Info($"[MarketStatus] {pair} market is {status}, next_open={nextBegin:yyyy-MM-dd HH:mm:ss}");
            }
        }
    }
}
