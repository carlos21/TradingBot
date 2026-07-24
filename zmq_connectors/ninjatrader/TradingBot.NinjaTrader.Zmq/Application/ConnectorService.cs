using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application
{
    /// <summary>
    /// Headless orchestration service for the ZMQ connector.
    /// Contains all business logic without any dependency on NinjaTrader APIs.
    /// </summary>
    public sealed class ConnectorService : IDisposable, IConnectorService
    {
        private readonly ZmqConfiguration _config;
        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly CommandDispatcher _dispatcher;
        private readonly IOrderTracker _orderTracker;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly IAccountProvider _accountProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IPnLCalculator _pnlCalculator;
        private readonly IConnectorClock _clock;
        private readonly ITradeIdExtractor _tradeIdExtractor;

        private Thread _commandThread;
        private Thread _heartbeatThread;
        private Thread _safetyThread;
        private Thread _connectionWatchdogThread;
        private CancellationTokenSource _cts;

        private readonly int _watchdogIntervalMs;
        private readonly int _watchdogFailureThreshold;
        private readonly int _watchdogPingTimeoutMs;
        private readonly int _watchdogBackoffThreshold;
        private readonly int _watchdogBackoffIntervalMs;

        /// <summary>
        /// When true, the background safety loop will flatten any open position
        /// that does not have a working stop-loss attached. This is the last-line
        /// defense against the partial-fill / missing-bracket scenario.
        /// </summary>
        public bool SafetyGuardEnabled { get; set; } = true;

        /// <summary>
        /// Interval between safety checks. Default is 3 seconds. Can be shortened
        /// in tests to exercise the loop without waiting.
        /// </summary>
        public int SafetyCheckIntervalMs { get; set; } = 3000;

        private volatile bool _connected;
        private readonly object _connectLock = new object();
        private readonly object _seqNumLock = new object();

        private long _commandsReceived = 0;

        private readonly HashSet<int> _processedSeqNums = new HashSet<int>();
        private readonly Queue<int> _processedSeqNumQueue = new Queue<int>();
        private const int MAX_TRACKED_SEQ_NUMS = 1000;

        // Prevents the safety guard from submitting duplicate flatten orders for the
        // same orphan position while waiting for the market fill to arrive.
        private readonly Dictionary<string, DateTime> _recentOrphanFlattens = new Dictionary<string, DateTime>();
        private static readonly TimeSpan OrphanFlattenCooldown = TimeSpan.FromSeconds(30);

        // Gives the bracket orders a chance to reach "Working" state after an entry fill
        // before the safety guard treats the position as unprotected.
        private readonly Dictionary<string, DateTime> _recentEntryFills = new Dictionary<string, DateTime>();
        private static readonly TimeSpan EntryFillGracePeriod = TimeSpan.FromSeconds(2);

        public bool IsConnected => _connected;
        public IZmqNetwork Network => _network;
        public string Pair => string.IsNullOrEmpty(_streamingCoordinator?.CurrentInstrument)
            ? "" : _streamingCoordinator.CurrentInstrument.Split(' ')[0];

        public ConnectorService(
            ZmqConfiguration config,
            IZmqNetwork network,
            ILogger logger,
            CommandDispatcher dispatcher,
            IOrderTracker orderTracker,
            IStreamingCoordinator streamingCoordinator,
            IAccountProvider accountProvider,
            IOrderExecutionService orderExecutionService,
            IInstrumentProvider instrumentProvider,
            IBarHistoryService barHistoryService,
            IPnLCalculator pnlCalculator,
            IConnectorClock clock,
            ITradeIdExtractor tradeIdExtractor,
            int watchdogIntervalMs = 5000,
            int watchdogFailureThreshold = 3,
            int watchdogPingTimeoutMs = 2000,
            int watchdogBackoffThreshold = 3,
            int watchdogBackoffIntervalMs = 300000)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _streamingCoordinator = streamingCoordinator ?? throw new ArgumentNullException(nameof(streamingCoordinator));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
            _orderExecutionService = orderExecutionService ?? throw new ArgumentNullException(nameof(orderExecutionService));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _barHistoryService = barHistoryService ?? throw new ArgumentNullException(nameof(barHistoryService));
            _pnlCalculator = pnlCalculator ?? throw new ArgumentNullException(nameof(pnlCalculator));
            _clock = clock ?? throw new ArgumentNullException(nameof(clock));
            _tradeIdExtractor = tradeIdExtractor ?? throw new ArgumentNullException(nameof(tradeIdExtractor));
            _watchdogIntervalMs = watchdogIntervalMs;
            _watchdogFailureThreshold = watchdogFailureThreshold;
            _watchdogPingTimeoutMs = watchdogPingTimeoutMs;
            _watchdogBackoffThreshold = watchdogBackoffThreshold;
            _watchdogBackoffIntervalMs = watchdogBackoffIntervalMs;
        }

        public void Connect()
        {
            lock (_connectLock)
            {
                if (_connected)
                {
                    _logger.Warning("Already connected, ignoring connect request");
                    return;
                }

                try
                {
                    _logger.Info("Starting ZeroMQ connection...");

                    _network.Start();
                    _cts = new CancellationTokenSource();
                    _connected = true;

                    _clock.Sleep(300);

                    _network.SendConnect("ninjatrader", _config.PlatformVersion, pair: Pair);
                    _logger.Success("Connected to Python TradingBot via ZeroMQ");
                    if (string.IsNullOrEmpty(Pair))
                        _logger.Info("Waiting for subscribe command from Python with the instrument to use");

                    RestoreOrderTracking();
                    ReportPositionsToPython();

                    _commandThread = new Thread(CommandLoop) { IsBackground = true, Name = "ZMQ-Commands" };
                    _commandThread.Start();

                    _heartbeatThread = new Thread(HeartbeatLoop) { IsBackground = true, Name = "ZMQ-Heartbeat" };
                    _heartbeatThread.Start();

                    _safetyThread = new Thread(SafetyLoop) { IsBackground = true, Name = "ZMQ-Safety" };
                    _safetyThread.Start();
                    _logger.Info($"Safety guard started (enabled={SafetyGuardEnabled}, interval={SafetyCheckIntervalMs}ms)");

                    _connectionWatchdogThread = new Thread(ConnectionWatchdogLoop) { IsBackground = true, Name = "ZMQ-ConnWatchdog" };
                    _connectionWatchdogThread.Start();
                    _logger.Info($"Connection watchdog started (interval={_watchdogIntervalMs}ms, threshold={_watchdogFailureThreshold})");
                }
                catch (Exception ex)
                {
                    _logger.Error("Connection error", ex);
                    _network.SendError("ninjatrader", "connection_failed", ex.Message, FormatExceptionDetails(ex));
                    Disconnect("connection error");
                }
            }
        }

        public void Disconnect(string reason = null)
        {
            lock (_connectLock)
            {
                if (reason != null)
                    _logger?.Info($"Disconnecting: {reason}");

                _connected = false;
                _cts?.Cancel();

                _streamingCoordinator?.Stop();
                _clock.Sleep(100);

                if (_commandThread != null && _commandThread.IsAlive)
                {
                    _commandThread.Join(600);
                    _commandThread = null;
                }
                if (_heartbeatThread != null && _heartbeatThread.IsAlive)
                {
                    _heartbeatThread.Join(600);
                    _heartbeatThread = null;
                }
                if (_safetyThread != null && _safetyThread.IsAlive)
                {
                    _safetyThread.Join(600);
                    _safetyThread = null;
                }
                if (_connectionWatchdogThread != null && _connectionWatchdogThread.IsAlive)
                {
                    _connectionWatchdogThread.Join(600);
                    _connectionWatchdogThread = null;
                }

                _network?.Stop();
                _orderTracker?.Clear();

                lock (_seqNumLock)
                {
                    _processedSeqNums.Clear();
                    _processedSeqNumQueue.Clear();
                }

                _cts?.Dispose();
                _cts = null;

                _commandsReceived = 0;

                _logger?.Info("Disconnected from Python TradingBot");
            }
        }

        public void Dispose() => Disconnect("disposing");

        private void RestoreOrderTracking()
        {
            foreach (var account in _accountProvider.GetAccounts())
            {
                var orders = _orderExecutionService.GetAllOrders(account);
                foreach (var order in orders)
                {
                    string tradeId = _tradeIdExtractor.ExtractTradeId(order.Name);
                    if (string.IsNullOrEmpty(tradeId)) continue;

                    if (_tradeIdExtractor.IsEntryOrder(order.Name))
                    {
                        _orderTracker.TrackEntry(tradeId, order);
                        _logger.Info($"[Recovery] Restored entry order for {tradeId}");
                    }
                    else if (_tradeIdExtractor.IsStopOrder(order.Name))
                    {
                        _orderTracker.TrackStopLoss(tradeId, order);
                        _logger.Info($"[Recovery] Restored stop order for {tradeId}");
                    }
                    else if (_tradeIdExtractor.IsTargetOrder(order.Name))
                    {
                        _orderTracker.TrackTakeProfit(tradeId, order);
                        _logger.Info($"[Recovery] Restored target order for {tradeId}");
                    }
                    else if (_tradeIdExtractor.IsCloseOrder(order.Name))
                    {
                        _orderTracker.TrackCloseOrder(tradeId, order);
                        _logger.Info($"[Recovery] Restored close order for {tradeId}");
                    }
                }
            }
        }

        private void ReportPositionsToPython()
        {
            try
            {
                var positions = new JArray();
                var trackedTradeIds = new HashSet<string>(_orderTracker.GetActiveTradeIds());

                foreach (var tradeId in trackedTradeIds)
                {
                    if (!_orderTracker.TryGetEntry(tradeId, out var entryOrder))
                        continue;

                    _orderTracker.TryGetStopLoss(tradeId, out var stopOrder);
                    _orderTracker.TryGetTakeProfit(tradeId, out var targetOrder);

                    var position = new JObject
                    {
                        ["trade_id"] = tradeId,
                        ["direction"] = entryOrder.OrderSide == OrderSide.Buy ? "long" : "short",
                        ["entry_price"] = entryOrder.AverageFillPrice,
                        ["quantity"] = entryOrder.Filled > 0 ? entryOrder.Filled : entryOrder.Quantity,
                        ["order_state"] = entryOrder.OrderState.ToString(),
                        ["account"] = entryOrder.AccountName,
                    };

                    if (stopOrder != null)
                        position["stop_loss"] = stopOrder.StopPrice;
                    if (targetOrder != null)
                        position["take_profit"] = targetOrder.LimitPrice;

                    positions.Add(position);
                }

                var untrackedOrders = new JArray();
                foreach (var account in _accountProvider.GetAccounts())
                {
                    foreach (var order in _orderExecutionService.GetWorkingOrders(account))
                    {
                        string tradeIdFromName = _tradeIdExtractor.ExtractTradeId(order.Name);
                        if (!string.IsNullOrEmpty(tradeIdFromName) && !trackedTradeIds.Contains(tradeIdFromName))
                        {
                            untrackedOrders.Add(new JObject
                            {
                                ["order_name"] = order.Name,
                                ["trade_id"] = tradeIdFromName,
                                ["order_type"] = order.OrderType.ToString(),
                                ["account"] = account.Name,
                            });
                        }
                    }
                }

                _logger.Info($"[Sync] Reporting {positions.Count} position(s) to Python (broker is source of truth)");

                if (positions.Count > 0 || untrackedOrders.Count > 0)
                    _network.SendPositionSync(positions, untrackedOrders);

                _logger.Success($"[Sync] Complete: {positions.Count} positions reported");

                if (untrackedOrders.Count > 0)
                    _logger.Warning($"[Sync] Found {untrackedOrders.Count} untracked orders on broker");
            }
            catch (Exception ex)
            {
                _logger.Error("[Sync] Error reporting positions to Python", ex);
            }
        }

        private void CommandLoop()
        {
            _logger.Info("Command loop started");

            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                MessageEnvelope envelope = null;
                string tradeId = null;
                try
                {
                    envelope = _network?.ReceiveCommand(timeoutMs: 100);
                    if (envelope == null) continue;

                    if (IsDuplicateCommand(envelope.SeqNum))
                    {
                        _logger.Warning($"Duplicate command ignored: {envelope.MsgType} seq={envelope.SeqNum}");
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, true, message: "duplicate");
                        continue;
                    }

                    _commandsReceived++;

                    try { tradeId = envelope.Payload?["trade_id"]?.ToString(); }
                    catch (Exception ex) { _logger.Warning($"Failed to extract trade_id from envelope: {ex.Message}"); }

                    if (_dispatcher == null)
                    {
                        _logger.Error("Dispatcher is null, cannot process command");
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, false, tradeId, "dispatcher not available");
                        continue;
                    }

                    bool dispatchSuccess = _dispatcher.Dispatch(envelope);
                    if (dispatchSuccess)
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, true, tradeId);
                    else
                    {
                        _logger.Error($"Command dispatch failed: {envelope.MsgType}");
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, false, tradeId, "handler returned failure");
                        _network?.SendError("ninjatrader", "command_dispatch_failed", $"{envelope.MsgType}: handler returned failure");
                    }

                }
                catch (Exception ex)
                {
                    _logger.Error("Command loop error", ex);
                    try
                    {
                        _network?.SendCommandAck(
                            envelope?.MsgType ?? "unknown",
                            envelope?.SeqNum ?? 0,
                            false,
                            tradeId,
                            $"Command loop error: {ex.Message}");
                    }
                    catch (Exception ackEx)
                    {
                        _logger.Error("Failed to send error ack", ackEx);
                    }
                    _network?.SendError("ninjatrader", "command_loop_error", ex.Message, FormatExceptionDetails(ex));
                }
            }

            _logger.Info("Command loop stopped");
        }

        private bool IsDuplicateCommand(int seqNum)
        {
            if (seqNum <= 0) return false;

            lock (_seqNumLock)
            {
                if (_processedSeqNums.Contains(seqNum))
                    return true;

                _processedSeqNums.Add(seqNum);

                if (_processedSeqNums.Count > MAX_TRACKED_SEQ_NUMS)
                {
                    int evictCount = MAX_TRACKED_SEQ_NUMS / 5;
                    for (int i = 0; i < evictCount && _processedSeqNumQueue.Count > 0; i++)
                        _processedSeqNums.Remove(_processedSeqNumQueue.Dequeue());
                }
                _processedSeqNumQueue.Enqueue(seqNum);

                return false;
            }
        }

        private void HeartbeatLoop()
        {
            _clock.Sleep(500);

            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    _network?.SendHeartbeat("ninjatrader", "ok");

                    for (int i = 0; i < 50 && _connected && !_cts.Token.IsCancellationRequested; i++)
                        _clock.Sleep(100);
                }
                catch (Exception ex)
                {
                    _logger.Warning("Heartbeat error: " + ex.Message);
                    _network?.SendError("ninjatrader", "heartbeat_error", ex.Message);
                }
            }
        }

        private void SafetyLoop()
        {
            // Use real Thread.Sleep rather than _clock.Sleep so the production
            // guard keeps running even if the clock abstraction is mocked.
            Thread.Sleep(SafetyCheckIntervalMs);

            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    if (SafetyGuardEnabled)
                        RunSafetyCheckOnce();
                }
                catch (Exception ex)
                {
                    _logger.Error("Safety guard error", ex);
                }

                Thread.Sleep(SafetyCheckIntervalMs);
            }
        }

        /// <summary>
        /// Detects a dead Python command channel (e.g. after the Python backend or
        /// the whole WSL VM restarts while NinjaTrader keeps running). The passive
        /// command PullSocket never notices the half-open TCP connection on its own,
        /// so every Python command times out. Ping the query channel (REQ/REP);
        /// after too many consecutive failures, recreate all ZMQ sockets so the
        /// connector reconnects. After recreation we deliberately do NOT re-send
        /// SendConnect or re-subscribe: Python's retry loop re-sends subscribe+refresh.
        /// </summary>
        private void ConnectionWatchdogLoop()
        {
            int consecutiveFailures = 0;
            int consecutiveCycles = 0;
            int currentIntervalMs = _watchdogIntervalMs;
            bool backingOff = false;

            // Use real Thread.Sleep (in slices) rather than _clock.Sleep so the
            // watchdog keeps running even if the clock abstraction is mocked.
            SleepWatchdog(currentIntervalMs);

            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    bool reachable = false;
                    try
                    {
                        reachable = _network != null && _network.SendTestPingWithResponse(_watchdogPingTimeoutMs);
                    }
                    catch (Exception pingEx)
                    {
                        _logger.Warning($"Connection watchdog ping error: {pingEx.Message}");
                    }

                    if (reachable)
                    {
                        consecutiveFailures = 0;
                        consecutiveCycles = 0;
                        if (backingOff)
                        {
                            backingOff = false;
                            currentIntervalMs = _watchdogIntervalMs;
                            _logger.Info("Python reachable again — connection watchdog back to normal ping interval");
                        }
                    }
                    else
                    {
                        consecutiveFailures++;
                        if (consecutiveFailures >= _watchdogFailureThreshold)
                        {
                            _logger.Warning($"Python unreachable on query channel after {consecutiveFailures} failed pings — recreating ZMQ sockets");
                            try
                            {
                                // Do NOT wrap this in any lock the watchdog holds: the ping
                                // and ReceiveCommand acquire the network's internal locks and
                                // release them on their own (ReceiveCommand holds the recv lock
                                // for at most ~100ms, so Stop() blocking briefly is fine).
                                _network.Stop();
                                _network.Start();
                            }
                            catch (Exception restartEx)
                            {
                                _logger.Error("Connection watchdog failed to recreate ZMQ sockets", restartEx);
                            }
                            consecutiveFailures = 0;

                            consecutiveCycles++;
                            if (!backingOff && consecutiveCycles >= _watchdogBackoffThreshold)
                            {
                                backingOff = true;
                                currentIntervalMs = _watchdogBackoffIntervalMs;
                                _logger.Warning($"Python still unreachable — backing off to {currentIntervalMs}ms ping interval");
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger.Error("Connection watchdog error", ex);
                }

                SleepWatchdog(currentIntervalMs);
            }
        }

        /// <summary>
        /// Sleeps in short slices so the loop still wakes promptly on
        /// cancellation/Disconnect even when the backoff interval is minutes long
        /// (mirrors the heartbeat loop's sliced sleep).
        /// </summary>
        private void SleepWatchdog(int milliseconds)
        {
            int slept = 0;
            while (slept < milliseconds && _connected && !_cts.Token.IsCancellationRequested)
            {
                int slice = Math.Min(100, milliseconds - slept);
                Thread.Sleep(slice);
                slept += slice;
            }
        }

        /// <summary>
        /// One-shot safety check:
        /// 1. Any tracked entry with filled contracts but no working stop-loss is
        ///    flattened only if the broker still holds the position.
        /// 2. Any orphan account position on the configured instrument without a
        ///    working stop-loss is flattened immediately.
        /// Exposed publicly so tests can exercise the guard without waiting for the
        /// background loop.
        /// </summary>
        public void RunSafetyCheckOnce()
        {
            var accounts = _accountProvider.GetAccounts() ?? new List<BrokerAccount>();
            CleanupRecentEntryFills();
            var accountPositions = new List<BrokerPosition>();
            foreach (var account in accounts)
            {
                try
                {
                    var positions = _orderExecutionService.GetAccountPositions(account) ?? new List<BrokerPosition>();
                    accountPositions.AddRange(positions);
                }
                catch (Exception ex)
                {
                    _logger.Error($"Safety guard: failed to read positions for {account.Name}", ex);
                }
            }

            // 1. Tracked entries: flatten if no working SL and the broker still holds the position.
            foreach (var tradeId in _orderTracker.GetActiveTradeIds().ToList())
            {
                if (!_orderTracker.TryGetEntry(tradeId, out var entryOrder))
                    continue;

                if (entryOrder.Filled <= 0)
                    continue;

                if (entryOrder.OrderState == OrderState.Cancelled ||
                    entryOrder.OrderState == OrderState.Rejected)
                    continue;

                if (_orderTracker.IsClosePending(tradeId))
                    continue;

                bool hasWorkingStop = false;
                if (_orderTracker.TryGetStopLoss(tradeId, out var trackedStop) && trackedStop.IsWorking)
                {
                    var account = _accountProvider.GetAccount(entryOrder.AccountName);
                    if (account != null)
                    {
                        var workingOrders = _orderExecutionService.GetWorkingOrders(account) ?? new List<BrokerOrder>();
                        hasWorkingStop = workingOrders.Any(o =>
                            _tradeIdExtractor.IsStopOrder(o.Name) &&
                            _tradeIdExtractor.ExtractTradeId(o.Name) == tradeId &&
                            o.IsWorking);
                    }
                    else
                    {
                        hasWorkingStop = true; // trust tracker if account lookup fails
                    }
                }

                if (hasWorkingStop)
                    continue;

                // Do not flatten a position that is already flat at the broker.
                var actualPosition = accountPositions.FirstOrDefault(p =>
                    p.AccountName == entryOrder.AccountName &&
                    p.Instrument?.MasterInstrumentName == entryOrder.Instrument?.MasterInstrumentName &&
                    (entryOrder.OrderSide == OrderSide.Buy ? p.IsLong : p.IsShort));
                if (actualPosition == null || actualPosition.Quantity <= 0)
                {
                    // The broker position is already flat but the tracker still holds the entry.
                    // Remove the stale tracker entry so the safety guard stops checking it and
                    // the warning does not spam every safety-check cycle.
                    _logger.Debug($"SAFETY GUARD: Entry {tradeId} has no working stop-loss, but the account position is flat. Removing stale tracker entry.");
                    _orderTracker.RemoveTrade(tradeId);
                    continue;
                }

                if (IsWithinEntryFillGracePeriod(tradeId))
                {
                    _logger.Debug($"SAFETY GUARD: Entry {tradeId} was just filled; waiting {EntryFillGracePeriod.TotalSeconds}s for bracket to become working.");
                    continue;
                }

                var instrumentName = entryOrder.Instrument?.MasterInstrumentName ?? entryOrder.Instrument?.Name ?? "unknown";
                var trackedSl = _orderTracker.TryGetStopLoss(tradeId, out var ts) ? ts.StopPrice.ToString("F2") : "none";
                _logger.Error(
                    $"SAFETY GUARD: Entry {tradeId} on {instrumentName}/{entryOrder.AccountName} " +
                    $"has {entryOrder.Filled} filled contract(s) @ {entryOrder.AverageFillPrice:F2} " +
                    $"with no working stop-loss (tracked SL={trackedSl}). Flattening immediately.");
                _network.SendError("ninjatrader", "missing_stop_loss_guard",
                    $"Entry {tradeId} ({instrumentName}/{entryOrder.AccountName}) has {entryOrder.Filled} filled contract(s) " +
                    $"@ {entryOrder.AverageFillPrice:F2} without a working stop-loss — flattening");
                _network.SendTradeLog(tradeId, "NT:SAFETY_GUARD",
                    $"Flattening {entryOrder.Filled} contracts on {instrumentName}/{entryOrder.AccountName}: no working stop-loss");

                var pendingEntry = GetPendingEntryOrFallback(tradeId, entryOrder);
                FlattenPosition(entryOrder, pendingEntry, tradeId, "Safety guard: no working stop-loss");
            }

            // 2. Account-level sweep: flatten any orphan position without a working stop-loss.
            var currentPair = Pair;
            foreach (var position in accountPositions)
            {
                if (position.Quantity == 0)
                    continue;

                if (!string.IsNullOrEmpty(currentPair) &&
                    position.Instrument?.MasterInstrumentName != currentPair)
                    continue;

                var account = accounts.FirstOrDefault(a => a.Name == position.AccountName);
                if (account == null)
                    continue;

                var workingOrders = _orderExecutionService.GetWorkingOrders(account) ?? new List<BrokerOrder>();
                if (HasWorkingStopForPosition(position, workingOrders))
                    continue;

                // If a tracked entry exists for this same instrument/account, the tracked
                // loop above already handled it (either it has a working stop or it was flattened).
                bool hasTrackedEntry = _orderTracker.GetActiveTradeIds().Any(id =>
                {
                    if (!_orderTracker.TryGetEntry(id, out var e))
                        return false;
                    return e.AccountName == position.AccountName &&
                           e.Instrument?.MasterInstrumentName == position.Instrument?.MasterInstrumentName &&
                           (position.IsLong ? e.OrderSide == OrderSide.Buy : e.OrderSide == OrderSide.Sell || e.OrderSide == OrderSide.SellShort);
                });
                if (hasTrackedEntry)
                    continue;

                FlattenAccountPosition(account, position, "Safety guard: no working stop-loss");
            }
        }

        private PendingEntryInfo GetPendingEntryOrFallback(string tradeId, BrokerOrder entryOrder)
        {
            if (_orderTracker.TryGetPendingEntry(tradeId, out var pendingEntry))
                return pendingEntry;

            string direction = entryOrder.OrderSide == OrderSide.Buy ? "long" : "short";
            return new PendingEntryInfo(direction, 0, 0);
        }

        // ═══════════════════════════════════════════════════════════════════
        // Order / Execution Updates (called by the presentation layer adapter)
        // ═══════════════════════════════════════════════════════════════════

        public void OnOrderUpdate(BrokerOrder order)
        {
            try
            {
                if (order == null)
                {
                    _logger.Warning("Order update with no associated order");
                    return;
                }

                _logger.Info($"ORDER UPDATE: {order.Name} state={order.OrderState} account={order.AccountName}");
                string tradeIdFromName = _tradeIdExtractor.ExtractTradeId(order.Name);

                if (_tradeIdExtractor.IsStopOrder(order.Name))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackStopLoss(tradeIdFromName, order);
                        _logger.Info($"TRACKING stop order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Stop order '{order.Name}' has no trade_id in name - cannot track!");
                        _network.SendError("ninjatrader", "order_tracking_failed", $"Stop order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (_tradeIdExtractor.IsTargetOrder(order.Name))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackTakeProfit(tradeIdFromName, order);
                        _logger.Info($"TRACKING target order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Target order '{order.Name}' has no trade_id in name - cannot track!");
                        _network.SendError("ninjatrader", "order_tracking_failed", $"Target order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (_tradeIdExtractor.IsEntryOrder(order.Name))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackEntry(tradeIdFromName, order);
                        _logger.Info($"TRACKING entry order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Entry order '{order.Name}' has no trade_id in name - cannot track!");
                        _network.SendError("ninjatrader", "order_tracking_failed", $"Entry order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (_tradeIdExtractor.IsCloseOrder(order.Name))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackCloseOrder(tradeIdFromName, order);
                        _logger.Info($"TRACKING close order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Close order '{order.Name}' has no trade_id in name - cannot track!");
                        _network.SendError("ninjatrader", "order_tracking_failed", $"Close order '{order.Name}' missing trade_id in name");
                    }
                }

                if (order.OrderState == OrderState.Cancelled &&
                    (_tradeIdExtractor.IsStopOrder(order.Name) || _tradeIdExtractor.IsTargetOrder(order.Name)))
                {
                    HandleCancelledBracketOrder(order);
                }

                if (order.OrderState == OrderState.Rejected || order.OrderState == OrderState.Cancelled)
                {
                    string oid = _tradeIdExtractor.ExtractTradeId(order.Name);

                    if (order.OrderState == OrderState.Rejected && _tradeIdExtractor.IsEntryOrder(order.Name) && !string.IsNullOrEmpty(oid))
                        _orderTracker.RemoveTrade(oid);

                    if (order.OrderState == OrderState.Cancelled && _tradeIdExtractor.IsEntryOrder(order.Name) && !string.IsNullOrEmpty(oid))
                    {
                        if (_orderTracker.IsClosePending(oid))
                        {
                            _logger.Info($"[Close-Pending] Entry cancel confirmed for {oid} — cleaning up tracking");
                            _orderTracker.RemoveTrade(oid);
                        }
                        else if (order.Filled > 0 && _orderTracker.TryGetPendingEntry(oid, out var pendingEntry))
                        {
                            // Market order was partially filled then cancelled: the remaining contracts
                            // were never filled, but the filled portion has no bracket. Flatten immediately.
                            _logger.Warning($"CRITICAL: Entry {oid} cancelled after partial fill ({order.Filled}/{order.Quantity}). Flattening filled portion immediately.");
                            _network.SendError("ninjatrader", "partial_fill_cancelled", $"Entry {oid} cancelled after partial fill — flattening");
                            FlattenPosition(order, pendingEntry, oid, "Entry cancelled after partial fill");
                        }
                    }

                    if (order.OrderState == OrderState.Cancelled &&
                        (_tradeIdExtractor.IsStopOrder(order.Name) || _tradeIdExtractor.IsTargetOrder(order.Name)))
                    {
                        if (_orderTracker.IsExpectedCancellation(order.Name))
                        {
                            _orderTracker.RemoveExpectedCancellation(order.Name);
                            return;
                        }
                    }

                    if (ShouldSuppressBracketOrderError(order))
                    {
                        _logger.Info($"Suppressed expected bracket error for {order.Name} ({order.OrderState}) — trade is closing or closed");
                        return;
                    }

                    _network.SendError("ninjatrader", "order_state", $"Order {order.Name} is {order.OrderState}");
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Order update error", ex);
            }
        }

        private void HandleCancelledBracketOrder(BrokerOrder order)
        {
            string tid = _tradeIdExtractor.ExtractTradeId(order.Name);
            bool wasExpected = _orderTracker.IsExpectedCancellation(order.Name);
            string modifyKey = !string.IsNullOrEmpty(tid)
                ? (_tradeIdExtractor.IsStopOrder(order.Name) ? tid + ":sl" : tid + ":tp")
                : null;

            if (modifyKey == null || !_orderTracker.TryGetPendingModify(modifyKey, out var modInfo))
                return;

            if (!wasExpected)
            {
                _logger.Warning($"Order {order.Name} was cancelled unexpectedly. Discarding pending modify.");
                _orderTracker.RemovePendingModify(modifyKey);
                return;
            }

            if (!_orderTracker.TryGetEntry(tid, out _))
            {
                _logger.Warning($"Order {order.Name} cancelled but trade {tid} no longer active. Discarding pending modify.");
                _orderTracker.RemovePendingModify(modifyKey);
                return;
            }

            try
            {
                var account = ResolveAccountForOrder(order);
                if (account == null)
                {
                    _logger.Error($"Cannot create replacement order for {tid}: account not found");
                    _orderTracker.RemovePendingModify(modifyKey);
                    return;
                }

                BrokerOrder newOrder;
                if (modInfo.IsTarget)
                {
                    newOrder = _orderExecutionService.CreateTakeProfitOrder(
                        modInfo.Instrument, account, modInfo.OrderSide, modInfo.Quantity, modInfo.NewPrice, tid);
                }
                else
                {
                    newOrder = _orderExecutionService.CreateStopLossOrder(
                        modInfo.Instrument, account, modInfo.OrderSide, modInfo.Quantity, modInfo.NewPrice, tid);
                }

                if (newOrder != null)
                {
                    _orderExecutionService.SubmitOrder(newOrder);
                    _orderTracker.RemovePendingModify(modifyKey);
                    if (modInfo.IsTarget)
                    {
                        _orderTracker.TrackTakeProfit(tid, newOrder);
                        _logger.Success($"Modified TP for {tid} to {modInfo.NewPrice}");
                        _network.SendTradeLog(tid, "NT:MODIFY", $"Take profit changed to {modInfo.NewPrice}");
                    }
                    else
                    {
                        _orderTracker.TrackStopLoss(tid, newOrder);
                        _logger.Success($"Modified SL for {tid} to {modInfo.NewPrice}");
                        _network.SendTradeLog(tid, "NT:MODIFY", $"Stop loss changed to {modInfo.NewPrice}");
                    }
                }
                else
                {
                    _orderTracker.RemovePendingModify(modifyKey);
                    _logger.Error($"Failed to create replacement order for {tid}");
                    _network.SendError("ninjatrader", "order_modify_failed", $"Failed to create replacement for {tid}");
                }
            }
            catch (Exception modEx)
            {
                _orderTracker.RemovePendingModify(modifyKey);
                _logger.Error($"Error creating replacement order for {tid}", modEx);
                _network.SendError("ninjatrader", "order_modify_failed", $"Replacement failed for {tid}: {modEx.Message}");
            }
        }

        /// <summary>
        /// Suppresses Rejected/Cancelled error messages for bracket orders (stop/target)
        /// when the trade is already closing or closed. This avoids noisy OCO cancellation
        /// logs after a stop-loss or take-profit fills and cancels the opposing bracket leg.
        /// </summary>
        private bool ShouldSuppressBracketOrderError(BrokerOrder order)
        {
            if (!_tradeIdExtractor.IsStopOrder(order.Name) && !_tradeIdExtractor.IsTargetOrder(order.Name))
                return false;

            string tid = _tradeIdExtractor.ExtractTradeId(order.Name);
            if (string.IsNullOrEmpty(tid))
                return false;

            if (_orderTracker.IsClosePending(tid))
                return true;

            if (!_orderTracker.TryGetEntry(tid, out var entry))
                return true;

            if (entry.OrderState != OrderState.Filled && entry.OrderState != OrderState.PartFilled)
                return true;

            return false;
        }

        public void OnExecutionUpdate(BrokerOrder order, double fillPrice, int quantity)
        {
            try
            {
                if (order == null)
                {
                    _logger.Warning("Execution update with no associated order");
                    return;
                }

                string execTradeId = _tradeIdExtractor.ExtractTradeId(order.Name) ?? order.Name;
                _logger.Info($"EXECUTION: {order.Name} @ {fillPrice} qty={quantity} account={order.AccountName}");
                _network.SendTradeLog(execTradeId, "NT:EXECUTION", $"Execution: {quantity} @ {fillPrice}");

                if (_tradeIdExtractor.IsEntryOrder(order.Name))
                    HandleEntryFill(order, fillPrice);
                else if (_tradeIdExtractor.IsStopOrder(order.Name))
                    HandleStopLossFill(order, fillPrice);
                else if (_tradeIdExtractor.IsTargetOrder(order.Name))
                    HandleTakeProfitFill(order, fillPrice);
                else if (_tradeIdExtractor.IsCloseOrder(order.Name))
                    HandleCloseFill(order, fillPrice);
                else
                    HandlePotentialManualClose(order, fillPrice);
            }
            catch (Exception ex)
            {
                _logger.Error("Execution update error", ex);
            }
        }

        private void HandleEntryFill(BrokerOrder order, double fillPrice)
        {
            string tradeId = _tradeIdExtractor.ExtractTradeId(order.Name);

            // Protect the position as soon as any contracts are filled. Large market
            // orders can be filled in multiple partial executions; waiting for the
            // order to reach the Filled state leaves the position exposed.
            if (order.Filled <= 0)
            {
                _logger.Info($"Entry {order.Name} state={order.OrderState} ({order.Filled}/{order.Quantity}) — no fills yet, skipping bracket.");
                return;
            }

            if (order.OrderState == OrderState.PartFilled)
            {
                _logger.Warning($"Entry {order.Name} partial fill ({order.Filled}/{order.Quantity}) — attaching protective bracket now.");
                _network.SendTradeLog(tradeId, "NT:WARNING", $"Partial entry fill {order.Filled}/{order.Quantity} — bracket attached");
            }

            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetPendingEntry(tradeId, out var entry))
            {
                _logger.Warning($"PendingEntryInfo missing for {tradeId} — querying Python for trade details (crash recovery)");
                entry = TryRecoverPendingEntryFromPython(tradeId);
                if (entry == null)
                {
                    _logger.Error($"CRITICAL: Entry fill for order '{order.Name}' not found in tracking AND Python query failed! Position has NO SL/TP!");
                    _network.SendError("ninjatrader", "fill_tracking_failed",
                        $"Entry fill for order '{order.Name}' not recoverable — UNPROTECTED POSITION");
                    return;
                }
                _logger.Success($"[Recovery] Recovered PendingEntryInfo for {tradeId} from Python: dir={entry.Direction} sl={entry.SlPoints} rr={entry.RrRatio}");
            }

            if (_orderTracker.IsClosePending(tradeId))
            {
                _logger.Warning($"[Close-Pending] Entry {tradeId} filled @ {fillPrice} despite cancel — flattening position immediately");
                FlattenPosition(order, entry, tradeId, "Close-pending entry filled");
                return;
            }

            var account = ResolveAccountForOrder(order);
            if (account == null)
            {
                _logger.Error($"CRITICAL: Entry fill for {tradeId} — account not found!");
                _network.SendError("ninjatrader", "fill_tracking_failed",
                    $"Entry fill for {tradeId}: account not found");
                return;
            }

            var (sl, tp) = CalculateSlTp(order.AverageFillPrice, entry.Direction, entry.SlPoints, entry.RrRatio);

            if (order.Instrument == null)
            {
                _logger.Error($"CRITICAL: Entry fill for {tradeId} has no instrument — cannot create bracket");
                _network.SendError("ninjatrader", "bracket_creation_failed", $"Entry fill for {tradeId}: instrument is null");
                return;
            }

            // If a bracket already exists (e.g., from a previous partial-fill attempt), replace it
            // so the quantity matches the final filled amount and prices match the actual fill.
            CancelWorkingBracketOrders(tradeId, account.Name);

            try
            {
                bool isLong = entry.Direction == "long";
                var closeSide = isLong ? OrderSide.Sell : OrderSide.BuyToCover;
                int qty = order.Filled > 0 ? order.Filled : order.Quantity;

                var stopOrder = _orderExecutionService.CreateStopLossOrder(order.Instrument, account, closeSide, qty, sl, tradeId);
                var targetOrder = _orderExecutionService.CreateTakeProfitOrder(order.Instrument, account, closeSide, qty, tp, tradeId);

                if (stopOrder == null || targetOrder == null)
                {
                    _logger.Error($"CRITICAL: Failed to create complete bracket for {tradeId} (stop={stopOrder != null}, target={targetOrder != null}). Flattening position immediately.");
                    _network.SendError("ninjatrader", "bracket_creation_failed", $"Incomplete bracket for {tradeId} — flattening");
                    FlattenPosition(order, entry, tradeId, "Incomplete bracket — stop/target creation failed");
                    return;
                }

                _orderExecutionService.SubmitOrders(new List<BrokerOrder> { stopOrder, targetOrder });
                _orderTracker.TrackStopLoss(tradeId, stopOrder);
                _orderTracker.TrackTakeProfit(tradeId, targetOrder);

                _logger.Success($"BRACKET CREATED: {tradeId} SL={sl} TP={tp} qty={qty} account={account.Name}");
                _network.SendTradeLog(tradeId, "NT:ORDER", $"Bracket created: SL={sl} TP={tp} qty={qty}");
            }
            catch (Exception bracketEx)
            {
                _logger.Error($"CRITICAL: Failed to create bracket orders for {tradeId}. Flattening position immediately.", bracketEx);
                _network.SendError("ninjatrader", "bracket_creation_failed", $"Failed to create SL/TP for {tradeId}: {bracketEx.Message}");
                FlattenPosition(order, entry, tradeId, $"Bracket creation exception: {bracketEx.Message}");
                return;
            }

            _logger.Success($"ENTRY FILL: {tradeId} @ {order.AverageFillPrice} SL={sl} TP={tp} qty={order.Filled} account={account.Name} balance={account.CashValue:C2}");
            _network.SendEntryFill(tradeId, order.AverageFillPrice, sl, tp, account: account.Name, quantity: order.Filled, accountBalance: account.CashValue);
            _network.SendTradeLog(tradeId, "NT:FILL", $"Entry filled @ {fillPrice} balance={account.CashValue:C2}");

            _recentEntryFills[tradeId] = _clock.UtcNow;
        }

        private PendingEntryInfo TryRecoverPendingEntryFromPython(string tradeId)
        {
            try
            {
                var positions = _network.QueryPositions(timeoutMs: 3000);
                if (positions == null || positions.Count == 0) return null;

                foreach (var pos in positions)
                {
                    var posTradeId = pos["trade_id"]?.ToString();
                    if (posTradeId != tradeId) continue;

                    var direction = pos["direction"]?.ToString();
                    var sl = pos["stop_loss"]?.Value<double>() ?? 0;
                    var tp = pos["take_profit"]?.Value<double>() ?? 0;
                    var entryPrice = pos["entry_price"]?.Value<double>() ?? 0;

                    if (string.IsNullOrEmpty(direction) || entryPrice <= 0 || sl <= 0)
                    {
                        _logger.Warning($"[Recovery] Python position for {tradeId} has incomplete data: dir={direction} entry={entryPrice} sl={sl}");
                        return null;
                    }

                    double slPoints = Math.Abs(entryPrice - sl);
                    double risk = slPoints;
                    double rrRatio = risk > 0 && tp > 0 ? Math.Abs(tp - entryPrice) / risk : 5.0;

                    return new PendingEntryInfo(direction, slPoints, rrRatio);
                }

                _logger.Warning($"[Recovery] Trade {tradeId} not found in Python positions");
                return null;
            }
            catch (Exception ex)
            {
                _logger.Error($"[Recovery] Failed to query Python for {tradeId}: {ex.Message}");
                return null;
            }
        }

        private void HandleStopLossFill(BrokerOrder order, double fillPrice)
        {
            string tradeId = _tradeIdExtractor.ExtractTradeId(order.Name);
            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetStopLoss(tradeId, out _))
            {
                _logger.Error($"CRITICAL: SL fill for order '{order.Name}' not found in tracking!");
                _network.SendError("ninjatrader", "fill_tracking_failed", $"SL fill for order '{order.Name}' not found in tracking");
                return;
            }

            // Mark close-pending as soon as the SL starts filling so that any OCO
            // rejection/cancellation of the opposing target order is treated as expected.
            _orderTracker.MarkClosePending(tradeId);

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Stop {order.Name} state={order.OrderState}, waiting for full fill.");
                return;
            }

            var entryOrder = GetEntryForExit(tradeId);
            var pnl = _pnlCalculator.Calculate(entryOrder, order);
            var account = ResolveAccountForOrder(order);
            var balanceInfo = account != null ? $" balance={account.CashValue:C2}" : "";
            _logger.Warning($"EXIT FILL (SL): {tradeId} @ {fillPrice} account={order.AccountName} pnl={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            _network.SendExitFill(tradeId, fillPrice, "SL", account: order.AccountName, realizedPnl: pnl?.RealizedPnl, accountBalance: account?.CashValue);
            _network.SendTradeLog(tradeId, "NT:FILL", $"SL filled @ {fillPrice} PnL={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            CancelWorkingBracketOrders(tradeId, order.AccountName);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleTakeProfitFill(BrokerOrder order, double fillPrice)
        {
            string tradeId = _tradeIdExtractor.ExtractTradeId(order.Name);
            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetTakeProfit(tradeId, out _))
            {
                _logger.Error($"CRITICAL: TP fill for order '{order.Name}' not found in tracking!");
                _network.SendError("ninjatrader", "fill_tracking_failed", $"TP fill for order '{order.Name}' not found in tracking");
                return;
            }

            // Mark close-pending as soon as the TP starts filling so that any OCO
            // rejection/cancellation of the opposing stop order is treated as expected.
            _orderTracker.MarkClosePending(tradeId);

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Target {order.Name} state={order.OrderState}, waiting for full fill.");
                return;
            }

            var entryOrder = GetEntryForExit(tradeId);
            var pnl = _pnlCalculator.Calculate(entryOrder, order);
            var account = ResolveAccountForOrder(order);
            var balanceInfo = account != null ? $" balance={account.CashValue:C2}" : "";
            _logger.Success($"EXIT FILL (TP): {tradeId} @ {fillPrice} account={order.AccountName} pnl={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            _network.SendExitFill(tradeId, fillPrice, "TP", account: order.AccountName, realizedPnl: pnl?.RealizedPnl, accountBalance: account?.CashValue);
            _network.SendTradeLog(tradeId, "NT:FILL", $"TP filled @ {fillPrice} PnL={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            CancelWorkingBracketOrders(tradeId, order.AccountName);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleCloseFill(BrokerOrder order, double fillPrice)
        {
            string tradeId = _tradeIdExtractor.ExtractTradeId(order.Name);
            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetCloseOrder(tradeId, out _))
            {
                _logger.Error($"CRITICAL: Close fill for order '{order.Name}' not found in tracking!");
                _network.SendError("ninjatrader", "fill_tracking_failed", $"Close fill for order '{order.Name}' not found in tracking");
                return;
            }

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Close {order.Name} state={order.OrderState}, waiting for full fill.");
                return;
            }

            var entryOrder = GetEntryForExit(tradeId);
            var pnl = _pnlCalculator.Calculate(entryOrder, order);
            var account = ResolveAccountForOrder(order);
            var balanceInfo = account != null ? $" balance={account.CashValue:C2}" : "";
            _logger.Success($"POSITION CLOSED: {tradeId} @ {fillPrice} account={order.AccountName} pnl={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            _network.SendExitFill(tradeId, fillPrice, "CLOSE", account: order.AccountName, realizedPnl: pnl?.RealizedPnl, accountBalance: account?.CashValue);
            _network.SendTradeLog(tradeId, "NT:FILL", $"Position closed @ {fillPrice} PnL={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
            CancelWorkingBracketOrders(tradeId, order.AccountName);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandlePotentialManualClose(BrokerOrder closeOrder, double fillPrice)
        {
            if (closeOrder?.Instrument == null) return;

            foreach (var tradeId in _orderTracker.GetActiveTradeIds())
            {
                if (!_orderTracker.TryGetEntry(tradeId, out var entryOrder)) continue;
                if (entryOrder.Instrument?.MasterInstrumentName != closeOrder.Instrument.MasterInstrumentName) continue;
                if (entryOrder.OrderState != OrderState.Filled && entryOrder.OrderState != OrderState.PartFilled) continue;
                if (!string.IsNullOrEmpty(closeOrder.AccountName) && !string.IsNullOrEmpty(entryOrder.AccountName) &&
                    closeOrder.AccountName != entryOrder.AccountName) continue;

                bool isOpposing = false;
                if (entryOrder.OrderSide == OrderSide.Buy && closeOrder.OrderSide == OrderSide.Sell)
                    isOpposing = true;
                else if (entryOrder.OrderSide == OrderSide.SellShort && closeOrder.OrderSide == OrderSide.BuyToCover)
                    isOpposing = true;

                if (isOpposing)
                {
                    var pnl = _pnlCalculator.Calculate(entryOrder, closeOrder);
                    var account = ResolveAccountForOrder(closeOrder);
                    var balanceInfo = account != null ? $" balance={account.CashValue:C2}" : "";
                    _logger.Success($"MANUAL CLOSE DETECTED: {tradeId} @ {fillPrice} via {closeOrder.Name} account={closeOrder.AccountName} pnl={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
                    _network.SendExitFill(tradeId, fillPrice, "CLOSE", account: closeOrder.AccountName, realizedPnl: pnl?.RealizedPnl, accountBalance: account?.CashValue);
                    _network.SendTradeLog(tradeId, "NT:FILL", $"Manual position closed @ {fillPrice} PnL={pnl?.RealizedPnl.ToString("F2") ?? "n/a"}{balanceInfo}");
                    CancelWorkingBracketOrders(tradeId, closeOrder.AccountName);
                    _orderTracker.RemoveTrade(tradeId);
                    return;
                }
            }
        }

        private void FlattenPosition(BrokerOrder entryOrder, PendingEntryInfo entry, string tradeId, string reason)
        {
            try
            {
                if (entryOrder.Filled <= 0)
                {
                    _logger.Warning($"Flatten requested for {tradeId} but entry has 0 filled contracts. Skipping.");
                    return;
                }

                if (_orderTracker.TryGetCloseOrder(tradeId, out _))
                {
                    _logger.Warning($"Flatten requested for {tradeId} but a close order is already active ({reason}). Ignoring duplicate.");
                    return;
                }

                var account = ResolveAccountForOrder(entryOrder);
                if (account == null || entryOrder.Instrument == null)
                {
                    _logger.Error($"CRITICAL: Cannot flatten {tradeId}: account or instrument missing ({reason})");
                    _network.SendError("ninjatrader", "flatten_failed", $"Cannot flatten {tradeId}: {reason}");
                    return;
                }

                bool isLong = entry.Direction == "long";
                var flatSide = isLong ? OrderSide.Sell : OrderSide.BuyToCover;
                int flatQty = entryOrder.Filled > 0 ? entryOrder.Filled : entryOrder.Quantity;

                var flatOrder = _orderExecutionService.CreateMarketCloseOrder(entryOrder.Instrument, account, flatSide, flatQty, tradeId);
                if (flatOrder != null)
                {
                    _orderTracker.TrackCloseOrder(tradeId, flatOrder);
                    _orderExecutionService.SubmitOrder(flatOrder);
                    _logger.Success($"FLATTENED {tradeId} on {entryOrder.Instrument?.MasterInstrumentName}/{account.Name}: {flatSide} {flatQty} contracts ({reason})");
                    _network.SendTradeLog(tradeId, "NT:FLATTEN", $"Flattened {flatQty} contracts on {entryOrder.Instrument?.MasterInstrumentName}/{account.Name}: {reason}");
                }
                else
                {
                    _logger.Error($"CRITICAL: CreateMarketCloseOrder returned null for {tradeId} ({reason})");
                    _network.SendError("ninjatrader", "flatten_failed", $"CreateMarketCloseOrder null for {tradeId}");
                }
            }
            catch (Exception ex)
            {
                _logger.Error($"CRITICAL: Exception flattening {tradeId} ({reason})", ex);
                _network.SendError("ninjatrader", "flatten_failed", $"Exception flattening {tradeId}: {ex.Message}");
            }
            finally
            {
                _orderTracker.RemoveTrade(tradeId);
            }
        }

        private bool HasWorkingStopForPosition(BrokerPosition position, IReadOnlyList<BrokerOrder> workingOrders)
        {
            foreach (var order in workingOrders)
            {
                if (!order.IsWorking)
                    continue;
                if (order.OrderType != OrderType.StopMarket && order.OrderType != OrderType.StopLimit)
                    continue;
                if (order.Instrument?.MasterInstrumentName != position.Instrument?.MasterInstrumentName)
                    continue;

                bool closesLong = position.IsLong && (order.OrderSide == OrderSide.Sell || order.OrderSide == OrderSide.SellShort);
                bool closesShort = position.IsShort && (order.OrderSide == OrderSide.Buy || order.OrderSide == OrderSide.BuyToCover);

                if (closesLong || closesShort)
                    return true;
            }
            return false;
        }

        private bool ShouldSkipOrphanFlatten(string accountName, string instrumentName)
        {
            var key = $"{accountName}|{instrumentName}";
            var now = DateTime.UtcNow;

            var expired = _recentOrphanFlattens
                .Where(kv => now - kv.Value > OrphanFlattenCooldown)
                .Select(kv => kv.Key)
                .ToList();
            foreach (var k in expired)
                _recentOrphanFlattens.Remove(k);

            return _recentOrphanFlattens.ContainsKey(key);
        }

        private bool IsWithinEntryFillGracePeriod(string tradeId)
        {
            if (!_recentEntryFills.TryGetValue(tradeId, out var fillTime))
                return false;
            return _clock.UtcNow - fillTime < EntryFillGracePeriod;
        }

        private void CleanupRecentEntryFills()
        {
            var now = _clock.UtcNow;
            var expired = _recentEntryFills
                .Where(kv => now - kv.Value > EntryFillGracePeriod)
                .Select(kv => kv.Key)
                .ToList();
            foreach (var k in expired)
                _recentEntryFills.Remove(k);
        }

        private void FlattenAccountPosition(BrokerAccount account, BrokerPosition position, string reason)
        {
            try
            {
                var instrumentName = position.Instrument?.MasterInstrumentName ?? position.Instrument?.Name ?? "unknown";
                if (ShouldSkipOrphanFlatten(account.Name, instrumentName))
                    return;

                var flatSide = position.IsLong ? OrderSide.Sell : OrderSide.BuyToCover;
                var tradeId = $"orphan_{Guid.NewGuid():N}";

                var flatOrder = _orderExecutionService.CreateMarketCloseOrder(position.Instrument, account, flatSide, position.Quantity, tradeId);
                if (flatOrder == null)
                {
                    _logger.Error($"CRITICAL: Could not create orphan flatten order for {instrumentName}/{account.Name}");
                    _network.SendError("ninjatrader", "flatten_failed", $"CreateMarketCloseOrder null for orphan {instrumentName}");
                    return;
                }

                _orderExecutionService.SubmitOrder(flatOrder);
                _recentOrphanFlattens[$"{account.Name}|{instrumentName}"] = DateTime.UtcNow;

                _logger.Error(
                    $"SAFETY GUARD: Flattened orphan {position.Direction} position of {position.Quantity} " +
                    $"{instrumentName} on {account.Name} ({reason}).");
                _network.SendError("ninjatrader", "missing_stop_loss_guard",
                    $"Orphan {position.Direction} position of {position.Quantity} {instrumentName} on {account.Name} flattened ({reason})");
                _network.SendTradeLog(tradeId, "NT:SAFETY_GUARD",
                    $"Flattened orphan {position.Quantity} contracts on {instrumentName}/{account.Name}: {reason}");
            }
            catch (Exception ex)
            {
                _logger.Error($"CRITICAL: Exception flattening orphan position on {account.Name}", ex);
                _network.SendError("ninjatrader", "flatten_failed", $"Exception flattening orphan position: {ex.Message}");
            }
        }

        private BrokerOrder GetEntryForExit(string tradeId)
        {
            _orderTracker.TryGetEntry(tradeId, out var entry);
            return entry;
        }

        private (double sl, double tp) CalculateSlTp(double fillPrice, string direction, double slPoints, double rrRatio)
        {
            var dir = direction?.ToLowerInvariant();
            if (dir != "long" && dir != "short")
                throw new ArgumentException($"Invalid direction '{direction}' — must be 'long' or 'short'");

            if (dir == "long")
                return (fillPrice - slPoints, fillPrice + (slPoints * rrRatio));
            return (fillPrice + slPoints, fillPrice - (slPoints * rrRatio));
        }

        private void CancelWorkingBracketOrders(string tradeId, string accountName)
        {
            var account = _accountProvider.GetAccount(accountName);
            if (account == null)
            {
                if (_orderTracker.TryGetEntry(tradeId, out var entryOrder))
                    account = _accountProvider.GetAccount(entryOrder.AccountName);
            }
            if (account == null) return;

            if (_orderTracker.TryGetStopLoss(tradeId, out var stopOrder) && stopOrder.IsWorking)
            {
                try
                {
                    _orderTracker.ExpectCancellation(stopOrder.Name);
                    _orderExecutionService.CancelOrder(stopOrder);
                    _logger.Info($"Cancelled working stop order for {tradeId}");
                }
                catch (Exception ex)
                {
                    _logger.Warning($"Failed to cancel stop order for {tradeId}: {ex.Message}");
                }
            }

            if (_orderTracker.TryGetTakeProfit(tradeId, out var targetOrder) && targetOrder.IsWorking)
            {
                try
                {
                    _orderTracker.ExpectCancellation(targetOrder.Name);
                    _orderExecutionService.CancelOrder(targetOrder);
                    _logger.Info($"Cancelled working target order for {tradeId}");
                }
                catch (Exception ex)
                {
                    _logger.Warning($"Failed to cancel target order for {tradeId}: {ex.Message}");
                }
            }
        }

        private BrokerAccount ResolveAccountForOrder(BrokerOrder order)
        {
            return _accountProvider.GetAccount(order.AccountName);
        }

        // ═══════════════════════════════════════════════════════════════════
        // Stats / Utilities
        // ═══════════════════════════════════════════════════════════════════

        public string GetStats()
        {
            var (ticks, bars, partials) = _streamingCoordinator?.GetStats() ?? (0, 0, 0);
            return $"Ticks: {ticks} | Bars: {bars} | Partial: {partials} | Cmds: {_commandsReceived}";
        }

        public async Task<bool> TestConnectionAsync()
        {
            _logger.Info("=== TEST CONNECTION ===");
            try
            {
                bool success = await Task.Run(() => _network?.SendTestPingWithResponse(2000) ?? false);
                if (success)
                    _logger.Success("TEST CONNECTION: PASSED - ZMQ REQ/REP working");
                else
                    _logger.Warning("TEST CONNECTION: FAILED - No response from Python");
                return success;
            }
            catch (Exception ex)
            {
                _logger.Error("TEST CONNECTION: FAILED", ex);
                return false;
            }
        }

        private static string FormatExceptionDetails(Exception ex)
        {
            var sb = new StringBuilder();
            sb.AppendLine($"Exception: {ex.GetType().Name}");
            sb.AppendLine($"Message: {ex.Message}");
            sb.AppendLine($"StackTrace: {ex.StackTrace}");
            if (ex.InnerException != null)
            {
                sb.AppendLine($"InnerException: {ex.InnerException.GetType().Name}");
                sb.AppendLine($"InnerMessage: {ex.InnerException.Message}");
            }
            return sb.ToString();
        }
    }
}
