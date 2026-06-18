// TradingBotZmqConnector.cs — NinjaScript AddOn (ZeroMQ Edition)
// Refactored using SOLID principles, Clean Architecture, and Design Patterns
//
// Architecture:
//   - Domain Layer: Contracts (IMessageSerializer, IOrderTracker, ICommandHandler), Value Objects
//   - Infrastructure Layer: Implementations (JsonMessageSerializer, OrderStateManager, NinjatraderLogger)
//   - Application Layer: Services (ZmqNetwork, CommandDispatcher, ZmqE2ETestRunner)
//   - Presentation Layer: UI (ZmqConnectorWindow)
//   - Commands Layer: Command handlers (OrderOpenHandler, OrderCloseHandler, etc.)
//
// Design Patterns Used:
//   - Strategy: IMessageSerializer, IOrderTracker, ILogger, ICommandHandler
//   - Chain of Responsibility: CommandDispatcher
//   - Factory: MessageEnvelope.Create(), ZmqConfiguration
//   - Adapter: NinjatraderLogger
//   - Facade: ZmqNetwork (hides ZMQ complexity)
//   - Value Object: ZmqConfiguration, MessageEnvelope, PendingEntryInfo, TickEventArgs
//
// Multi-Account Support:
//   One ZMQ connector manages multiple NinjaTrader accounts. Python sends "account"
//   in command payloads; the connector routes to the correct Account object.
//
// Installation:
//   1. Download NetMQ.dll and Newtonsoft.Json.dll
//   2. Copy DLLs to: Documents\NinjaTrader 8\bin\Custom\
//   3. In NinjaTrader: Tools > Edit NinjaScript > right-click References > Add...
//   4. Add references to: NetMQ.dll, Newtonsoft.Json.dll
//   5. Right-click AddOns > New > AddOn, paste this code, compile (F5)
//   6. Click New > Liquid ZMQ Connector in the Control Center

#region Using declarations
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Main AddOn class - orchestrates all components using dependency injection.
    /// Thin controller that delegates to specialized services.
    /// Supports multi-account trading via a single ZMQ connection.
    /// </summary>
    public class TradingBotZmqConnector : AddOnBase
    {
        // Configuration (immutable value object)
        private readonly ZmqConfiguration _config;

        // Dependencies (injected)
        private ZmqNetwork _network;
        private ZmqConnectorWindow _ui;
        private ILogger _logger;
        private CommandDispatcher _dispatcher;
        private IOrderTracker _orderTracker;
        private IStreamingCoordinator _streamingCoordinator;

        // Background threads
        private Thread _commandThread;
        private Thread _heartbeatThread;
        private CancellationTokenSource _cts;

        // State
        private volatile bool _connected;
        private string Pair => string.IsNullOrEmpty(_streamingCoordinator?.CurrentInstrument)
            ? "" : _streamingCoordinator.CurrentInstrument.Split(' ')[0];

        // When true, ALL command handlers force simulate mode (no real orders)
        internal static volatile bool E2ETestRunning = false;

        // Stats
        private long _commandsReceived = 0;
        private long _barsSent = 0;

        // History / gap-fill tracking
        private DateTime _lastHistoryBarTime = DateTime.MinValue;

        // Duplicate command detection (track processed seq_nums)
        private readonly HashSet<int> _processedSeqNums = new HashSet<int>();
        private readonly Queue<int> _processedSeqNumQueue = new Queue<int>();  // For bounded eviction
        private readonly object _seqNumLock = new object();
        private const int MAX_TRACKED_SEQ_NUMS = 1000;  // Prevent memory growth

        // Connection lock to prevent double-connect / double-disconnect races
        private readonly object _connectLock = new object();



        // UI
        private MenuItem _menuItem;
        private MenuItem _existingNewMenu;

        public TradingBotZmqConnector()
        {
            // Load configuration from JSON file if present; otherwise use defaults.
            // This lets users toggle auto-connect without recompiling.
            _config = ConfigLoader.Load();
        }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Connects to TradingBot Python app via ZeroMQ (Refactored)";
                Name = "TradingBotZmqConnector";
            }
        }

        protected override void OnWindowCreated(Window window)
        {
            if (!(window is ControlCenter cc)) return;
            _existingNewMenu = FindMenuItem(cc.MainMenu, "New");
            if (_existingNewMenu == null) return;

            _menuItem = new MenuItem { Header = "Liquid ZMQ Connector" };
            _menuItem.Click += OnMenuItemClick;
            _existingNewMenu.Items.Add(_menuItem);

            // Auto-launch and auto-connect when the Control Center loads.
            // This is safe at runtime (not triggered during NinjaScript compilation).
            if (_config.AutoConnectOnStartup)
            {
                try
                {
                    if (_config.AutoShowWindow)
                        ShowStatusWindow();
                    else
                        InitializeLoggerOnly();

                    // Small delay to let the UI thread settle before connecting
                    System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(new Action(() =>
                    {
                        if (!_connected)
                            Connect();
                    }), System.Windows.Threading.DispatcherPriority.Background);
                }
                catch (Exception ex)
                {
                    Print($"[ZMQ] Auto-connect failed: {ex.Message}");
                }
            }
        }

        protected override void OnWindowDestroyed(Window window)
        {
            if (window is ControlCenter)
            {
                Disconnect("NinjaTrader shutting down");
                if (_menuItem != null)
                {
                    _existingNewMenu?.Items.Remove(_menuItem);
                    _menuItem.Click -= OnMenuItemClick;
                    _menuItem = null;
                    _existingNewMenu = null;
                }
            }
        }

        private void OnMenuItemClick(object sender, RoutedEventArgs e) => ShowStatusWindow();

        private void ShowStatusWindow()
        {
            if (_ui == null)
            {
                _ui = new ZmqConnectorWindow(msg => Print("[ZMQ] " + msg));
                _logger = new NinjatraderLogger(msg => _ui.Log(msg));
                _ui.SetButtonHandlers(
                    onConnect: ToggleConnection,
                    onTestConnection: () => _ = TestConnectionAsync(),
                    onE2ETests: () => _ = RunE2ETestsAsync()
                );
            }
            _ui.Show(_connected);
        }

        /// <summary>
        /// Initializes the logger without showing the UI window.
        /// Used when AutoConnectOnStartup is true but AutoShowWindow is false.
        /// </summary>
        private void InitializeLoggerOnly()
        {
            if (_logger == null)
                _logger = new NinjatraderLogger(msg => Print("[ZMQ] " + msg));
        }

        private void ToggleConnection()
        {
            if (_connected) Disconnect("user requested");
            else Connect();
        }

        // ═══════════════════════════════════════════════════════════════════
        // Connection Management
        // ═══════════════════════════════════════════════════════════════════

        private void Connect()
        {
            lock (_connectLock)
            {
                // Prevent double-connect
                if (_connected)
                {
                    _logger.Warning("Already connected, ignoring connect request");
                    return;
                }

                try
                {
                    _logger.Info("Starting ZeroMQ connection...");

                // Initialize components with dependency injection
                _orderTracker = new OrderStateManager();
                _network = new ZmqNetwork(_config, new JsonMessageSerializer(_logger), _logger);

                _network.Start();
                _cts = new CancellationTokenSource();
                _connected = true;

                // Wait for ZMQ sockets to fully establish (slow joiner protection)
                // This ensures Python's SUB sockets are ready before we send messages
                Thread.Sleep(300);

                // Streaming coordinator owns market-data and live-bar subscriptions.
                // Created on the UI thread so it can marshal NinjaTrader UI work.
                _streamingCoordinator = new StreamingCoordinator(_network, _logger, _config);

                // Subscribe to execution and order updates on ALL accounts.
                // Account config travels per-trade in the command payload;
                // no handshake query is needed.
                foreach (var acct in Account.All)
                {
                    acct.ExecutionUpdate += OnExecutionUpdate;
                    acct.OrderUpdate += OnOrderUpdate;
                }

                // Create dispatcher
                _dispatcher = CreateCommandDispatcher();

                // Send connect handshake (minimal — account name is not needed).
                // The full instrument is provided later by Python via the subscribe command.
                var pair = Pair;
                _network.SendConnect("ninjatrader", _config.PlatformVersion, pair: pair);
                _logger.Success("Connected to Python TradingBot via ZeroMQ");
                if (string.IsNullOrEmpty(pair))
                    _logger.Info("Waiting for subscribe command from Python with the instrument to use");

                // Start background threads
                _commandThread = new Thread(CommandLoop) { IsBackground = true, Name = "ZMQ-Commands" };
                _commandThread.Start();

                _heartbeatThread = new Thread(HeartbeatLoop) { IsBackground = true, Name = "ZMQ-Heartbeat" };
                _heartbeatThread.Start();
                
                // Restore order tracking from broker after potential crash (for each account)
                foreach (var acct in Account.All)
                {
                    _orderTracker.RestoreFromBrokerOrders(acct, _logger);
                }

                // Report actual broker positions to Python (broker is source of truth)
                ReportPositionsToPython();

                // Market-data and live-bar subscriptions are deferred until Python
                // sends the subscribe command with the instrument.

                // Historical data is NOT sent automatically on connect.
                // Python requests it explicitly via REFRESH_REQUEST when needed.

                UpdateStats();
            }
            catch (Exception ex)
            {
                _logger.Error("Connection error", ex);
                _network?.SendError("ninjatrader", "connection_failed", ex.Message, FormatExceptionDetails(ex));
                Disconnect("connection error");
            }
            }
        }

        private void Disconnect(string reason = null)
        {
            lock (_connectLock)
            {
                if (reason != null)
                {
                    _logger?.Info($"Disconnecting: {reason}");
                }
                _connected = false;
                _cts?.Cancel();

                // Stop market-data/live-bar streaming BEFORE tearing down ZMQ sockets
                _streamingCoordinator?.Stop();

                // Unsubscribe from ALL account events
                foreach (var acct in Account.All)
                {
                    try { acct.ExecutionUpdate -= OnExecutionUpdate; } catch { }
                    try { acct.OrderUpdate -= OnOrderUpdate; } catch { }
                }

                // Allow in-flight background sends to drain before disposing sockets
                Thread.Sleep(100);

                // Wait for background threads to exit so we don't dispose sockets while they're in use
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

                _network?.Dispose();
                _network = null;

                _orderTracker?.Clear();
                _orderTracker = null;

                // Clear duplicate-command tracking so reconnects with fresh seq_nums work
                lock (_seqNumLock)
                {
                    _processedSeqNums.Clear();
                    _processedSeqNumQueue.Clear();
                }

                _cts?.Dispose();
                _cts = null;

                // Reset stats
                _commandsReceived = 0;
                _barsSent = 0;

                _logger?.Info("Disconnected from Python TradingBot");
                UpdateStats();
            }
        }

        /// <summary>
        /// Report actual broker positions to Python.
        /// Broker (NinjaTrader) is the source of truth - Python reconciles to match.
        /// Called after connect to sync state after potential crash.
        /// </summary>
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

                    // Get associated stop/target orders if available
                    _orderTracker.TryGetStopLoss(tradeId, out var stopOrder);
                    _orderTracker.TryGetTakeProfit(tradeId, out var targetOrder);

                    var position = new JObject
                    {
                        ["trade_id"] = tradeId,
                        ["direction"] = entryOrder.OrderAction == OrderAction.Buy ? "long" : "short",
                        ["entry_price"] = entryOrder.AverageFillPrice,
                        ["quantity"] = entryOrder.Filled > 0 ? entryOrder.Filled : entryOrder.Quantity,
                        ["order_state"] = entryOrder.OrderState.ToString(),
                        ["account"] = entryOrder.Account?.Name,
                    };

                    if (stopOrder != null)
                        position["stop_loss"] = stopOrder.StopPrice;
                    if (targetOrder != null)
                        position["take_profit"] = targetOrder.LimitPrice;

                    positions.Add(position);
                }

                // Also report any untracked working orders (orphan detection) across all accounts
                var untrackedOrders = new JArray();
                foreach (var acct in Account.All)
                {
                    foreach (var order in acct.Orders ?? System.Linq.Enumerable.Empty<Order>())
                        {
                            if (order.OrderState != OrderState.Working && order.OrderState != OrderState.Accepted)
                                continue;

                            string tradeIdFromName = ExtractTradeIdFromOrderName(order.Name);
                            if (!string.IsNullOrEmpty(tradeIdFromName) && !trackedTradeIds.Contains(tradeIdFromName))
                            {
                                untrackedOrders.Add(new JObject
                                {
                                    ["order_name"] = order.Name,
                                    ["trade_id"] = tradeIdFromName,
                                    ["order_type"] = order.OrderType.ToString(),
                                    ["account"] = acct.Name,
                                });
                            }
                        }
                    }

                _logger.Info($"[Sync] Reporting {positions.Count} position(s) to Python (broker is source of truth)");
                
                if (positions.Count > 0 || untrackedOrders.Count > 0)
                {
                    _network?.SendPositionSync(positions, untrackedOrders);
                }

                _logger.Success($"[Sync] Complete: {positions.Count} positions reported");
                
                if (untrackedOrders.Count > 0)
                {
                    _logger.Warning($"[Sync] Found {untrackedOrders.Count} untracked orders on broker");
                }
            }
            catch (Exception ex)
            {
                _logger.Error("[Sync] Error reporting positions to Python", ex);
            }
        }

        private CommandDispatcher CreateCommandDispatcher()
        {
            var dispatcher = new CommandDispatcher(_logger);
            // Register command handlers - Chain of Responsibility pattern
            bool simulate = _ui?.IsSimulateTradesEnabled ?? false;
            dispatcher.Register(new SubscribeHandler(_network, _logger, _streamingCoordinator));
            dispatcher.Register(new OrderOpenHandler(_network, _logger, _orderTracker, simulate));
            dispatcher.Register(new OrderCloseHandler(_network, _logger, _orderTracker, simulate));
            dispatcher.Register(new OrderModifyHandler(_network, _logger, _orderTracker, simulate));
            dispatcher.Register(new RefreshRequestHandler(_network, _logger, SendHistoryAsync));
            dispatcher.Register(new AuditRequestHandler(_network, _logger, () => _streamingCoordinator?.CurrentInstrument ?? ""));
            dispatcher.Register(new TestStartHandler(_network, _logger));
            return dispatcher;
        }

        // ═══════════════════════════════════════════════════════════════════
        // Background Threads
        // ═══════════════════════════════════════════════════════════════════

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

                    // Duplicate detection
                    if (IsDuplicateCommand(envelope.SeqNum))
                    {
                        _logger.Warning($"Duplicate command ignored: {envelope.MsgType} seq={envelope.SeqNum}");
                        // Still send ack so Python knows we processed it
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, true, message: "duplicate");
                        continue;
                    }

                    _commandsReceived++;
                    
                    // Extract trade_id from payload for ack
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
                    {
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, true, tradeId);
                    }
                    else
                    {
                        _logger.Error($"Command dispatch failed: {envelope.MsgType}");
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, false, tradeId, "handler returned failure");
                        _network?.SendError("ninjatrader", "command_dispatch_failed", $"{envelope.MsgType}: handler returned failure");
                    }

                    if (_commandsReceived % 10 == 0) UpdateStats();
                }
                catch (Exception ex)
                {
                    _logger.Error("Command loop error", ex);
                    // Always send COMMAND_ACK so Python doesn't timeout waiting
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

        /// <summary>
        /// Check if this command sequence number was already processed (duplicate detection).
        /// </summary>
        private bool IsDuplicateCommand(int seqNum)
        {
            if (seqNum <= 0) return false;  // Invalid seq_num, process anyway

            lock (_seqNumLock)
            {
                if (_processedSeqNums.Contains(seqNum))
                    return true;

                _processedSeqNums.Add(seqNum);

                // Prevent unbounded growth — evict oldest 20% instead of clearing everything
                if (_processedSeqNums.Count > MAX_TRACKED_SEQ_NUMS)
                {
                    int evictCount = MAX_TRACKED_SEQ_NUMS / 5;
                    for (int i = 0; i < evictCount && _processedSeqNumQueue.Count > 0; i++)
                    {
                        _processedSeqNums.Remove(_processedSeqNumQueue.Dequeue());
                    }
                }
                _processedSeqNumQueue.Enqueue(seqNum);

                return false;
            }
        }

        private void HeartbeatLoop()
        {
            // Wait for ZMQ subscription to establish (slow joiner protection)
            // Python's SUB socket needs time to connect and subscribe
            Thread.Sleep(500);
            
            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    _network?.SendHeartbeat("ninjatrader", "ok");
                    
                    // Sleep in smaller increments to respond faster to cancellation
                    for (int i = 0; i < 50 && _connected && !_cts.Token.IsCancellationRequested; i++)
                    {
                        Thread.Sleep(100);
                    }
                }
                catch (Exception ex)
                {
                    _logger.Warning("Heartbeat error: " + ex.Message);
                    _network?.SendError("ninjatrader", "heartbeat_error", ex.Message);
                }
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Streaming lifecycle (delegated to StreamingCoordinator)
        // ═══════════════════════════════════════════════════════════════════

        /// <summary>
        /// Result of a single historical-bar request attempt.
        /// </summary>
        private class HistoryAttemptResult
        {
            public bool Success { get; set; }
            public string ErrorMessage { get; set; }
            public List<JObject> Bars { get; set; } = new List<JObject>();
            public int Count => Bars?.Count ?? 0;
            public DateTime FirstTime { get; set; }
            public DateTime LastTime { get; set; }
        }

        /// <summary>
        /// Perform one BarsRequest for the given lookback window and return the collected bars.
        /// </summary>
        private async Task<HistoryAttemptResult> TryRequestHistoryAsync(Instrument instrument, int days)
        {
            var result = new HistoryAttemptResult();
            var tcs = new TaskCompletionSource<bool>();
            var startDateTime = DateTime.UtcNow.AddDays(-days);
            var endDateTime = DateTime.UtcNow;

            _logger.Info($"[History] DateTime range | days={days} | start={startDateTime:yyyy-MM-dd HH:mm:ss} UTC | end={endDateTime:yyyy-MM-dd HH:mm:ss} UTC | requesting...");

            var barsRequest = new BarsRequest(instrument, startDateTime, endDateTime)
            {
                BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                TradingHours = TradingHours.Get("Default 24 x 7")
            };

            try
            {
                barsRequest.Request((bars, errorCode, errorMessage) =>
                {
                    try
                    {
                        if (errorCode != ErrorCode.NoError)
                        {
                            _logger.Error($"BarsRequest failed: {errorMessage}");
                            result.ErrorMessage = errorMessage;
                            tcs.TrySetResult(false);
                            return;
                        }

                        if (bars?.Bars == null)
                        {
                            _logger.Error("BarsRequest returned null bars");
                            result.ErrorMessage = "null bars";
                            tcs.TrySetResult(false);
                            return;
                        }

                        result.Success = true;
                        int receivedCount = bars.Bars.Count;

                        if (receivedCount > 0)
                        {
                            result.FirstTime = bars.Bars.GetTime(0);
                            result.LastTime = bars.Bars.GetTime(receivedCount - 1);
                            var gapToNow = DateTime.Now - result.LastTime;
                            _logger.Info($"[History] BarsRequest returned {receivedCount} bars | first={result.FirstTime:yyyy-MM-dd HH:mm:ss} | last={result.LastTime:yyyy-MM-dd HH:mm:ss} | gapToNow={gapToNow.TotalSeconds:F0}s");
                        }
                        else
                        {
                            _logger.Warning("[History] BarsRequest returned 0 bars");
                        }

                        for (int i = 0; i < bars.Bars.Count; i++)
                        {
                            result.Bars.Add(new JObject
                            {
                                ["time"] = ToUnixSeconds(bars.Bars.GetTime(i)),
                                ["open"] = bars.Bars.GetOpen(i),
                                ["high"] = bars.Bars.GetHigh(i),
                                ["low"] = bars.Bars.GetLow(i),
                                ["close"] = bars.Bars.GetClose(i),
                                ["volume"] = (long)bars.Bars.GetVolume(i),
                                ["pair"] = Pair
                            });
                        }

                        tcs.TrySetResult(true);
                    }
                    catch (Exception callbackEx)
                    {
                        _logger.Error("BarsRequest callback error", callbackEx);
                        result.ErrorMessage = callbackEx.Message;
                        tcs.TrySetResult(false);
                    }
                });

                var timeoutTask = Task.Delay(TimeSpan.FromSeconds(30));
                var completedTask = await Task.WhenAny(tcs.Task, timeoutTask);
                if (completedTask == timeoutTask)
                {
                    _logger.Error("BarsRequest timed out after 30 seconds");
                    result.ErrorMessage = "timeout";
                }
            }
            finally
            {
                barsRequest?.Dispose();
            }

            return result;
        }

        /// <summary>
        /// Keep the last N days worth of 1m bars (days * 1440 bars).
        /// </summary>
        private List<JObject> TrimToLastSession(List<JObject> bars, int days = 1)
        {
            if (bars == null || bars.Count < 2)
                return bars;

            int maxBars = days * 24 * 60;
            if (bars.Count <= maxBars)
                return bars;

            var trimmed = bars.Skip(bars.Count - maxBars).ToList();
            var firstTime = DateTimeOffset.FromUnixTimeSeconds(trimmed[0]["time"].Value<int>()).UtcDateTime;
            var lastTime = DateTimeOffset.FromUnixTimeSeconds(trimmed[trimmed.Count - 1]["time"].Value<int>()).UtcDateTime;
            _logger.Info($"[History] Trimmed to last {days} day(s): {trimmed.Count} bars | first={firstTime:yyyy-MM-dd HH:mm:ss} | last={lastTime:yyyy-MM-dd HH:mm:ss} (removed {bars.Count - trimmed.Count} older bars)");
            return trimmed;
        }

        private async Task SendHistoryAsync(string instrumentName, int days = 30)
        {
            try
            {
                var instrument = Instrument.GetInstrument(instrumentName);
                if (instrument == null)
                {
                    _logger.Error($"Instrument '{instrumentName}' not found");
                    return;
                }
                var pair = instrumentName.Split(' ')[0];

                const int maxHistoryDays = 30;
                // Linear expansion: add one calendar day per attempt instead of doubling.
                // This avoids the exponential blow-up that loaded 8 days when the user
                // configured only 1 day.
                var attemptDaysList = new List<int>();
                for (int d = days; d <= maxHistoryDays; d++)
                {
                    attemptDaysList.Add(d);
                }

                // Notify Python that a refresh is starting so it buffers live bars
                _network?.SendRefreshStart();
                _logger.Info("Sending refresh_start");

                HistoryAttemptResult finalResult = null;
                int attempt = 0;
                int attemptDays = days;

                foreach (var nextDays in attemptDaysList)
                {
                    attempt++;
                    attemptDays = nextDays;

                    var result = await TryRequestHistoryAsync(instrument, attemptDays);
                    if (!result.Success)
                    {
                        _logger.Warning($"[History] Attempt {attempt}/{attemptDaysList.Count} failed: {result.ErrorMessage}");
                        if (attempt < attemptDaysList.Count)
                            await Task.Delay(TimeSpan.FromMilliseconds(250));
                        continue;
                    }

                    finalResult = result;
                    if (result.Count > 0)
                    {
                        _logger.Info($"[History] Attempt {attempt}/{attemptDaysList.Count} succeeded with {result.Count} bar(s)");
                        break;
                    }

                    if (attempt < attemptDaysList.Count)
                    {
                        _logger.Info($"[History] Attempt {attempt}/{attemptDaysList.Count} returned 0 bars — expanding lookback window to {attemptDaysList[attempt]} day(s) (+1 day)");
                        await Task.Delay(TimeSpan.FromMilliseconds(250));
                    }
                }

                if (finalResult == null || finalResult.Count == 0)
                {
                    _logger.Warning($"[History] No historical bars found after {attempt} attempt(s) up to {attemptDays} day(s)");
                    _network?.SendHistoryEnd();
                    return;
                }

                // When the market has been closed for several days, the search window
                // can span multiple sessions. Trim to the most recent contiguous session
                // so the chart/strategy only receives the last active session's bars.
                int barsBeforeTrim = finalResult.Count;
                finalResult.Bars = TrimToLastSession(finalResult.Bars, days);
                if (finalResult.Bars.Count > 0)
                {
                    finalResult.LastTime = DateTimeOffset.FromUnixTimeSeconds(finalResult.Bars[finalResult.Bars.Count - 1]["time"].Value<int>()).UtcDateTime;
                }

                // Send the collected bars in batches
                var batch = new List<JObject>();
                foreach (var bar in finalResult.Bars)
                {
                    batch.Add(bar);
                    if (batch.Count >= _config.BatchSize)
                    {
                        _network?.SendHistoryBatch(pair, batch, attemptDays);
                        batch.Clear();
                    }
                }
                if (batch.Count > 0)
                    _network?.SendHistoryBatch(pair, batch, attemptDays);

                _barsSent = finalResult.Count;
                _lastHistoryBarTime = finalResult.LastTime;
                _logger.Info($"Sent {finalResult.Count} historical bars ({attemptDays} days requested, {barsBeforeTrim - finalResult.Count} trimmed)");

                // Gap-fill: if history ends significantly before now, try to fetch missing bars
                if (_lastHistoryBarTime != DateTime.MinValue)
                {
                    var gapToNow = DateTime.Now - _lastHistoryBarTime;
                    if (gapToNow.TotalMinutes > 5)
                    {
                        _logger.Info($"[History] Detected {gapToNow.TotalMinutes:F0}m gap to now — attempting gap-fill from {_lastHistoryBarTime:yyyy-MM-dd HH:mm:ss} UTC");
                        _ = SendGapFillAsync(instrument, _lastHistoryBarTime, DateTime.UtcNow).ContinueWith(t =>
                        {
                            if (t.IsFaulted)
                                _logger.Error("Gap-fill failed", t.Exception?.GetBaseException());
                        }, TaskContinuationOptions.OnlyOnFaulted);
                    }
                }

                // Internal gap-fill: scan the returned history for holes and patch them.
                if (finalResult.Count >= 2)
                {
                    var nowUtc = DateTime.UtcNow;
                    var fourHoursAgo = nowUtc.AddHours(-4);
                    for (int i = 1; i < finalResult.Count; i++)
                    {
                        var prevTime = finalResult.Bars[i - 1]["time"].Value<int>();
                        var currTime = finalResult.Bars[i]["time"].Value<int>();
                        var gap = currTime - prevTime;
                        if (gap > 60)
                        {
                            // Skip expected exchange breaks (e.g. CME 60-min daily break)
                            if (gap >= 30 * 60)
                                continue;

                            var prevDateTime = DateTimeOffset.FromUnixTimeSeconds(prevTime).UtcDateTime;
                            var currDateTime = DateTimeOffset.FromUnixTimeSeconds(currTime).UtcDateTime;
                            // Only chase recent gaps; old holes are not critical for trading
                            if (prevDateTime < fourHoursAgo)
                                continue;

                            _logger.Info($"[History] Detected internal gap: {(gap / 60):F0}m between {prevDateTime:yyyy-MM-dd HH:mm:ss} and {currDateTime:yyyy-MM-dd HH:mm:ss} UTC — attempting gap-fill");
                            _ = SendGapFillAsync(instrument, prevDateTime, currDateTime).ContinueWith(t =>
                            {
                                if (t.IsFaulted)
                                    _logger.Error("Internal gap-fill failed", t.Exception?.GetBaseException());
                            }, TaskContinuationOptions.OnlyOnFaulted);
                        }
                    }
                }

                _network?.SendHistoryEnd();
            }
            catch (Exception ex)
            {
                _logger.Error("SendHistory error", ex);
                _network?.SendError("ninjatrader", "history_load_failed", "Failed to load history", FormatExceptionDetails(ex));
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Gap Fill
        // ═══════════════════════════════════════════════════════════════════

        /// <summary>
        /// Sends a targeted BarsRequest for the gap between lastHistoryBarTime and endTime.
        /// Retries up to 3 times and logs each attempt so we can see what NT returns.
        /// </summary>
        private async Task SendGapFillAsync(Instrument instrument, DateTime gapStart, DateTime gapEnd)
        {
            const int maxAttempts = 3;
            var pair = Pair;
            int totalGapBarsSent = 0;

            for (int attempt = 1; attempt <= maxAttempts; attempt++)
            {
                _logger.Info($"[GapFill] Attempt {attempt}/{maxAttempts} | requesting bars from {gapStart:yyyy-MM-dd HH:mm:ss} UTC to {gapEnd:yyyy-MM-dd HH:mm:ss} UTC");

                var tcs = new TaskCompletionSource<bool>();
                var gapBatch = new List<JObject>();
                int gapCount = 0;
                DateTime? gapFirst = null;
                DateTime? gapLast = null;

                var gapRequest = new BarsRequest(instrument, gapStart, gapEnd)
                {
                    BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                    TradingHours = TradingHours.Get("Default 24 x 7")
                };

                try
                {
                    gapRequest.Request((bars, errorCode, errorMessage) =>
                    {
                        try
                        {
                            if (errorCode != ErrorCode.NoError)
                            {
                                _logger.Error($"[GapFill] Attempt {attempt}/{maxAttempts} | BarsRequest failed: {errorMessage}");
                                tcs.TrySetResult(false);
                                return;
                            }

                            if (bars?.Bars == null)
                            {
                                _logger.Error($"[GapFill] Attempt {attempt}/{maxAttempts} | BarsRequest returned null bars");
                                tcs.TrySetResult(false);
                                return;
                            }

                            int receivedCount = bars.Bars.Count;
                            if (receivedCount > 0)
                            {
                                gapFirst = bars.Bars.GetTime(0);
                                gapLast = bars.Bars.GetTime(receivedCount - 1);

                                for (int i = 0; i < bars.Bars.Count; i++)
                                {
                                    var barTime = bars.Bars.GetTime(i);
                                    // Skip bars already covered by the primary history
                                    if (barTime <= gapStart)
                                        continue;

                                    gapBatch.Add(new JObject
                                    {
                                        ["time"] = ToUnixSeconds(barTime),
                                        ["open"] = bars.Bars.GetOpen(i),
                                        ["high"] = bars.Bars.GetHigh(i),
                                        ["low"] = bars.Bars.GetLow(i),
                                        ["close"] = bars.Bars.GetClose(i),
                                        ["volume"] = (long)bars.Bars.GetVolume(i),
                                        ["pair"] = pair
                                    });
                                    gapCount++;
                                }
                            }

                            _logger.Info($"[GapFill] Attempt {attempt}/{maxAttempts} | returned {receivedCount} bars | first={gapFirst:yyyy-MM-dd HH:mm:ss} | last={gapLast:yyyy-MM-dd HH:mm:ss} | unique_new={gapCount}");
                            tcs.TrySetResult(true);
                        }
                        catch (Exception callbackEx)
                        {
                            _logger.Error($"[GapFill] Attempt {attempt}/{maxAttempts} | callback error", callbackEx);
                            tcs.TrySetResult(false);
                        }
                    });

                    var timeoutTask = Task.Delay(TimeSpan.FromSeconds(15));
                    var completedTask = await Task.WhenAny(tcs.Task, timeoutTask);
                    if (completedTask == timeoutTask)
                    {
                        _logger.Warning($"[GapFill] Attempt {attempt}/{maxAttempts} | timed out after 15s");
                    }
                }
                finally
                {
                    gapRequest?.Dispose();
                }

                if (gapCount > 0)
                {
                    totalGapBarsSent += gapCount;
                    if (gapBatch.Count > 0)
                    {
                        _network?.SendHistoryBatch(pair, gapBatch, days: 1);
                        _barsSent += gapCount;
                        _logger.Success($"[GapFill] SUCCESS — sent {gapCount} bars on attempt {attempt}/{maxAttempts}");
                    }

                    // If the gap-fill didn't reach the end, update gapStart for next attempt.
                    // A remaining gap < 2 minutes is considered success — live stream covers it.
                    if (gapLast.HasValue && gapLast.Value < gapEnd.AddMinutes(-2))
                    {
                        gapStart = gapLast.Value;
                        _logger.Info($"[GapFill] Gap partially filled — continuing from {gapStart:yyyy-MM-dd HH:mm:ss} UTC");
                        continue; // retry with updated gapStart
                    }

                    return; // gap fully filled (or < 2 min remaining)
                }

                _logger.Warning($"[GapFill] Attempt {attempt}/{maxAttempts} | no new bars received");

                if (attempt < maxAttempts)
                {
                    await Task.Delay(TimeSpan.FromSeconds(2)); // brief delay before retry
                }
            }

            if (totalGapBarsSent > 0)
            {
                _logger.Info($"[GapFill] Sent {totalGapBarsSent} bars total. Remaining micro-gap < 2 min — live stream will cover it.");
            }
            else
            {
                _logger.Error($"[GapFill] FAILED after {maxAttempts} attempts — no bars received for any attempt. Gap remains from {gapStart:yyyy-MM-dd HH:mm:ss} UTC to {gapEnd:yyyy-MM-dd HH:mm:ss} UTC");
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Account / Order Management
        // ═══════════════════════════════════════════════════════════════════

        private static Account ResolveAccountForOrder(Order order)
        {
            return order?.Account;
        }

        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            try
            {
                var order = e.Order;
                if (order == null)
                {
                    _logger.Warning("Order update with no associated order");
                    return;
                }
                _logger.Info($"ORDER UPDATE: {order.Name} state={order.OrderState} account={order.Account?.Name}");

                // Extract trade_id from order name (e.g., "Stop_trade-123" -> "trade-123")
                string tradeIdFromName = ExtractTradeIdFromOrderName(order.Name);

                // Track orders by type - MUST have trade_id in name
                if (IsStopOrder(order))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackStopLoss(tradeIdFromName, order);
                        _logger.Info($"TRACKING stop order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Stop order '{order.Name}' has no trade_id in name - cannot track!");
                        _network?.SendError("ninjatrader", "order_tracking_failed", 
                            $"Stop order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (IsTargetOrder(order))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackTakeProfit(tradeIdFromName, order);
                        _logger.Info($"TRACKING target order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Target order '{order.Name}' has no trade_id in name - cannot track!");
                        _network?.SendError("ninjatrader", "order_tracking_failed", 
                            $"Target order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (IsEntryOrder(order))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackEntry(tradeIdFromName, order);
                        _logger.Info($"TRACKING entry order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Entry order '{order.Name}' has no trade_id in name - cannot track!");
                        _network?.SendError("ninjatrader", "order_tracking_failed", 
                            $"Entry order '{order.Name}' missing trade_id in name");
                    }
                }
                else if (IsCloseOrder(order))
                {
                    if (!string.IsNullOrEmpty(tradeIdFromName))
                    {
                        _orderTracker.TrackCloseOrder(tradeIdFromName, order);
                        _logger.Info($"TRACKING close order for {tradeIdFromName} (from name)");
                    }
                    else
                    {
                        _logger.Error($"CRITICAL: Close order '{order.Name}' has no trade_id in name - cannot track!");
                        _network?.SendError("ninjatrader", "order_tracking_failed", 
                            $"Close order '{order.Name}' missing trade_id in name");
                    }
                }

                // Process pending modify when a stop/target order is successfully cancelled
                if (order.OrderState == OrderState.Cancelled && (IsStopOrder(order) || IsTargetOrder(order)))
                {
                    string tid = ExtractTradeIdFromOrderName(order.Name);
                    bool wasExpected = _orderTracker.IsExpectedCancellation(order.Name);
                    // NOTE: Do NOT remove expected-cancellation here.
                    // The suppression check below handles it uniformly
                    // for both modify and close workflows.

                    // Use keyed slots: stop modifies use "tradeId:sl", target modifies use "tradeId:tp"
                    string modifyKey = !string.IsNullOrEmpty(tid) ? (IsStopOrder(order) ? tid + ":sl" : tid + ":tp") : null;
                    if (modifyKey != null && _orderTracker.TryGetPendingModify(modifyKey, out var modInfo))
                    {
                        if (!wasExpected)
                        {
                            _logger.Warning($"Order {order.Name} was cancelled unexpectedly (not by modify/close workflow). Discarding pending modify.");
                            _orderTracker.RemovePendingModify(modifyKey);
                        }
                        else if (!_orderTracker.TryGetEntry(tid, out _))
                        {
                            _logger.Warning($"Order {order.Name} cancelled but trade {tid} no longer active. Discarding pending modify.");
                            _orderTracker.RemovePendingModify(modifyKey);
                        }
                        else
                        {
                            try
                            {
                                var account = ResolveAccountForOrder(order);
                                if (account == null)
                                {
                                    _logger.Error($"Cannot create replacement order for {tid}: account not found");
                                    _orderTracker.RemovePendingModify(modifyKey);
                                    return;
                                }

                                Order newOrder;
                                if (modInfo.IsTarget)
                                {
                                    newOrder = account.CreateOrder(
                                        modInfo.Instrument,
                                        modInfo.OrderAction,
                                        OrderType.Limit,
                                        OrderEntry.Automated,
                                        TimeInForce.Gtc,
                                        modInfo.Quantity,
                                        modInfo.NewPrice,
                                        0,
                                        $"OCO_{tid}",
                                        $"Target_{tid}",
                                        DateTime.MinValue,
                                        null);
                                }
                                else
                                {
                                    newOrder = account.CreateOrder(
                                        modInfo.Instrument,
                                        modInfo.OrderAction,
                                        OrderType.StopMarket,
                                        OrderEntry.Automated,
                                        TimeInForce.Gtc,
                                        modInfo.Quantity,
                                        0,
                                        modInfo.NewPrice,
                                        $"OCO_{tid}",
                                        $"Stop_{tid}",
                                        DateTime.MinValue,
                                        null);
                                }

                                if (newOrder != null)
                                {
                                    account.Submit(new[] { newOrder });
                                    _orderTracker.RemovePendingModify(modifyKey);
                                    if (modInfo.IsTarget)
                                    {
                                        _orderTracker.TrackTakeProfit(tid, newOrder);
                                        _logger.Success($"Modified TP for {tid} to {modInfo.NewPrice}");
                                        _network?.SendTradeLog(tid, "NT:MODIFY", $"Take profit changed to {modInfo.NewPrice}");
                                    }
                                    else
                                    {
                                        _orderTracker.TrackStopLoss(tid, newOrder);
                                        _logger.Success($"Modified SL for {tid} to {modInfo.NewPrice}");
                                        _network?.SendTradeLog(tid, "NT:MODIFY", $"Stop loss changed to {modInfo.NewPrice}");
                                    }
                                }
                                else
                                {
                                    _orderTracker.RemovePendingModify(modifyKey);
                                    _logger.Error($"Failed to create replacement order for {tid}");
                                    _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to create replacement for {tid}");
                                }
                            }
                            catch (Exception modEx)
                            {
                                _orderTracker.RemovePendingModify(modifyKey);
                                _logger.Error($"Error creating replacement order for {tid}", modEx);
                                _network?.SendError("ninjatrader", "order_modify_failed", $"Replacement failed for {tid}: {modEx.Message}");
                            }
                        }
                    }
                }

                // Notify Python of rejected/cancelled orders
                if (order.OrderState == OrderState.Rejected || order.OrderState == OrderState.Cancelled)
                {
                    string oid = ExtractTradeIdFromOrderName(order.Name);

                    // Clean up rejected entry orders from tracking so they don't block future opens
                    if (order.OrderState == OrderState.Rejected && IsEntryOrder(order) && !string.IsNullOrEmpty(oid))
                    {
                        _orderTracker.RemoveTrade(oid);
                    }

                    // Cancel confirmed for a close-pending entry — no fill will come, safe to clean up
                    if (order.OrderState == OrderState.Cancelled && IsEntryOrder(order) && !string.IsNullOrEmpty(oid))
                    {
                        if (_orderTracker.IsClosePending(oid))
                        {
                            _logger.Info($"[Close-Pending] Entry cancel confirmed for {oid} — cleaning up tracking");
                            _orderTracker.RemoveTrade(oid);
                        }
                    }

                    // Only suppress cancelled notifications for stop/target orders that we EXPECTED to cancel
                    // (e.g., via Python close command or our own modify workflow). Unexpected cancellations
                    // (broker risk management, manual user cancel, margin issues) must be reported.
                    if (order.OrderState == OrderState.Cancelled && (IsStopOrder(order) || IsTargetOrder(order)))
                    {
                        if (_orderTracker.IsExpectedCancellation(order.Name))
                        {
                            _orderTracker.RemoveExpectedCancellation(order.Name);
                            return;  // Expected — suppress noise
                        }
                        // Unexpected cancellation — fall through to report as error below
                    }

                    _network?.SendError("ninjatrader", "order_state", $"Order {order.Name} is {order.OrderState}");
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Order update error", ex);
            }
        }

        private static string ExtractTradeIdFromOrderName(string orderName)
        {
            if (string.IsNullOrEmpty(orderName)) return null;
            
            if (orderName.StartsWith("Entry_"))
                return orderName.Substring(6);
            if (orderName.StartsWith("Stop_"))
                return orderName.Substring(5);
            if (orderName.StartsWith("Target_"))
                return orderName.Substring(7);
            if (orderName.StartsWith("Close_"))
                return orderName.Substring(6);
            
            return null;
        }

        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            try
            {
                var execution = e.Execution;
                var order = execution.Order;
                if (order == null)
                {
                    _logger.Warning("Execution update with no associated order");
                    return;
                }
                var fillPrice = execution.Price;

                string execTradeId = ExtractTradeIdFromOrderName(order.Name) ?? order.Name;
                _logger.Info($"EXECUTION: {order.Name} @ {fillPrice} qty={execution.Quantity} account={order.Account?.Name}");
                _network?.SendTradeLog(execTradeId, "NT:EXECUTION", $"Execution: {execution.Quantity} @ {fillPrice}");

                if (IsEntryOrder(order))
                {
                    HandleEntryFill(order, fillPrice);
                }
                else if (IsStopOrder(order))
                {
                    HandleStopLossFill(order, fillPrice);
                }
                else if (IsTargetOrder(order))
                {
                    HandleTakeProfitFill(order, fillPrice);
                }
                else if (IsCloseOrder(order))
                {
                    HandleCloseFill(order, fillPrice);
                }
                else
                {
                    // Catch manual closes that don't match our Close_{tradeId} naming.
                    // Look for an opposing execution against a tracked filled entry.
                    HandlePotentialManualClose(order, fillPrice);
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Execution update error", ex);
            }
        }

        private void HandleEntryFill(Order order, double fillPrice)
        {
            if (order.OrderState != OrderState.Filled)
            {
                _logger.Info($"Entry {order.Name} state={order.OrderState} ({order.Filled}/{order.Quantity}), waiting for full fill before creating bracket.");
                return;
            }

            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                // Fallback: extract trade_id directly from order name in case OnOrderUpdate hasn't tracked it yet
                tradeId = ExtractTradeIdFromOrderName(order.Name);
            }

            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetPendingEntry(tradeId, out var entry))
            {
                // PendingEntryInfo is lost (crash recovery scenario).
                // Query Python for the trade details so we can still create the SL/TP bracket.
                _logger.Warning($"PendingEntryInfo missing for {tradeId} — querying Python for trade details (crash recovery)");
                entry = TryRecoverPendingEntryFromPython(tradeId);
                if (entry == null)
                {
                    _logger.Error($"CRITICAL: Entry fill for order '{order.Name}' not found in tracking AND Python query failed! Position has NO SL/TP!");
                    _network?.SendError("ninjatrader", "fill_tracking_failed",
                        $"Entry fill for order '{order.Name}' not recoverable — UNPROTECTED POSITION");
                    return;
                }
                _logger.Success($"[Recovery] Recovered PendingEntryInfo for {tradeId} from Python: dir={entry.Direction} sl={entry.SlPoints} rr={entry.RrRatio}");
            }

            // If a close was already requested for this trade (e.g. session end fired before
            // the broker confirmed the cancel), skip bracket creation and immediately flatten.
            if (_orderTracker.IsClosePending(tradeId))
            {
                _logger.Warning($"[Close-Pending] Entry {tradeId} filled @ {fillPrice} despite cancel — flattening position immediately");
                var flatAccount = ResolveAccountForOrder(order);
                if (flatAccount != null && order.Instrument != null)
                {
                    bool isLong = entry.Direction == "long";
                    var flatAction = isLong ? OrderAction.Sell : OrderAction.BuyToCover;
                    int flatQty = order.Filled > 0 ? order.Filled : order.Quantity;
                    var flatOrder = flatAccount.CreateOrder(
                        order.Instrument, flatAction, OrderType.Market, OrderEntry.Automated,
                        TimeInForce.Gtc, flatQty, 0, 0, null, $"Close_{tradeId}", DateTime.MinValue, null);
                    if (flatOrder != null)
                    {
                        flatAccount.Submit(new[] { flatOrder });
                        _logger.Success($"[Close-Pending] Submitted market close for {tradeId}: {flatAction} {flatQty} contracts");
                    }
                }
                _network?.SendTradeLog(tradeId, "NT:CLOSE_PENDING_FILL", $"Entry filled @ {fillPrice} after close request — flattened immediately");
                _orderTracker.RemoveTrade(tradeId);
                return;
            }

            var account = ResolveAccountForOrder(order);
            if (account == null)
            {
                _logger.Error($"CRITICAL: Entry fill for {tradeId} — account not found!");
                _network?.SendError("ninjatrader", "fill_tracking_failed",
                    $"Entry fill for {tradeId}: account not found");
                return;
            }

            var (sl, tp) = CalculateSlTp(fillPrice, entry.Direction, entry.SlPoints, entry.RrRatio);

            // Create bracket orders manually (ATM strategies don't work reliably from AddOn context)
            if (order.Instrument != null && !_orderTracker.TryGetStopLoss(tradeId, out _))
            {
                try
                {
                    bool isLong = entry.Direction == "long";
                    var closeAction = isLong ? OrderAction.Sell : OrderAction.BuyToCover;
                    int qty = order.Filled > 0 ? order.Filled : order.Quantity;

                    string ocoId = $"OCO_{tradeId}";

                    var stopOrder = account.CreateOrder(
                        order.Instrument, closeAction, OrderType.StopMarket, OrderEntry.Automated, TimeInForce.Gtc,
                        qty, 0, sl, ocoId, $"Stop_{tradeId}", DateTime.MinValue, null);

                    var targetOrder = account.CreateOrder(
                        order.Instrument, closeAction, OrderType.Limit, OrderEntry.Automated, TimeInForce.Gtc,
                        qty, tp, 0, ocoId, $"Target_{tradeId}", DateTime.MinValue, null);

                    if (stopOrder != null)
                    {
                        account.Submit(new[] { stopOrder });
                        _orderTracker.TrackStopLoss(tradeId, stopOrder);
                    }
                    if (targetOrder != null)
                    {
                        account.Submit(new[] { targetOrder });
                        _orderTracker.TrackTakeProfit(tradeId, targetOrder);
                    }

                    if (stopOrder != null && targetOrder != null)
                    {
                        _logger.Success($"BRACKET CREATED: {tradeId} SL={sl} TP={tp} qty={qty} account={account.Name}");
                        _network?.SendTradeLog(tradeId, "NT:ORDER", $"Bracket created: SL={sl} TP={tp} qty={qty}");
                    }
                    else
                    {
                        _logger.Warning($"Partial bracket for {tradeId}: stop={(stopOrder != null)} target={(targetOrder != null)}");
                    }
                }
                catch (Exception bracketEx)
                {
                    _logger.Error($"Failed to create bracket orders for {tradeId}", bracketEx);
                    _network?.SendError("ninjatrader", "bracket_creation_failed", $"Failed to create SL/TP for {tradeId}: {bracketEx.Message}");
                }
            }

            string accountName = account?.Name;
            _logger.Success($"ENTRY FILL: {tradeId} @ {fillPrice} SL={sl} TP={tp} account={accountName}");
            _network?.SendEntryFill(tradeId, fillPrice, sl, tp, account: accountName);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"Entry filled @ {fillPrice}");
        }

        /// <summary>
        /// Crash recovery: query Python for trade details when PendingEntryInfo is lost.
        /// Returns a reconstructed PendingEntryInfo or null if recovery fails.
        /// </summary>
        private PendingEntryInfo TryRecoverPendingEntryFromPython(string tradeId)
        {
            try
            {
                var positions = _network?.QueryPositions(timeoutMs: 3000);
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

        /// <summary>
        /// Calculates the broker-reported realized PnL and commission for a closed trade.
        /// Uses the tracked entry order and the given exit order's actual average fill prices,
        /// filled quantity, instrument point value, and order commission (when available).
        /// Returns nulls if the entry order is not tracked or required prices are unavailable.
        /// </summary>
        private (double? realizedPnl, double? commission) CalculateExitPnl(Order exitOrder)
        {
            try
            {
                if (exitOrder == null)
                    return (null, null);

                var tradeId = ExtractTradeIdFromOrderName(exitOrder.Name) ?? exitOrder.Name;
                if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetEntry(tradeId, out var entryOrder) || entryOrder == null)
                    return (null, null);

                int quantity = exitOrder.Filled > 0 ? exitOrder.Filled : exitOrder.Quantity;
                if (quantity <= 0)
                    return (null, null);

                double entryPrice = entryOrder.AverageFillPrice;
                double exitPrice = exitOrder.AverageFillPrice;
                if (entryPrice == 0 || exitPrice == 0)
                    return (null, null);

                double pointValue = exitOrder.Instrument?.MasterInstrument?.PointValue ?? 2.0;
                bool isLong = entryOrder.OrderAction == OrderAction.Buy;
                double priceDiff = isLong ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
                double grossPnl = priceDiff * quantity * pointValue;

                double commission = 0.0;
                try
                {
                    commission = (entryOrder.Commission) + (exitOrder.Commission);
                }
                catch
                {
                    // Order.Commission may not be available in all NT versions; ignore.
                    commission = 0.0;
                }

                double realizedPnl = grossPnl - commission;
                return (realizedPnl, commission);
            }
            catch (Exception ex)
            {
                _logger?.Warning($"[CalculateExitPnl] Failed for {exitOrder?.Name}: {ex.Message}");
                return (null, null);
            }
        }

        private void HandleStopLossFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                tradeId = ExtractTradeIdFromOrderName(order.Name);
            }

            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetStopLoss(tradeId, out _))
            {
                _logger.Error($"CRITICAL: SL fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed",
                    $"SL fill for order '{order.Name}' not found in tracking");
                return;
            }

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Stop {order.Name} state={order.OrderState} ({order.Filled}/{order.Quantity}), waiting for full fill before processing SL exit.");
                return;
            }

            string accountName = order.Account?.Name;
            var (realizedPnl, commission) = CalculateExitPnl(order);
            _logger.Warning($"EXIT FILL (SL): {tradeId} @ {fillPrice} account={accountName} pnl={realizedPnl?.ToString("F2") ?? "n/a"}");
            _network?.SendExitFill(tradeId, fillPrice, "SL", account: accountName, realizedPnl: realizedPnl, commission: commission);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"SL filled @ {fillPrice} PnL={realizedPnl?.ToString("F2") ?? "n/a"}");
            CancelWorkingBracketOrders(tradeId, order.Account);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleTakeProfitFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                tradeId = ExtractTradeIdFromOrderName(order.Name);
            }

            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetTakeProfit(tradeId, out _))
            {
                _logger.Error($"CRITICAL: TP fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed",
                    $"TP fill for order '{order.Name}' not found in tracking");
                return;
            }

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Target {order.Name} state={order.OrderState} ({order.Filled}/{order.Quantity}), waiting for full fill before processing TP exit.");
                return;
            }

            string accountName = order.Account?.Name;
            var (realizedPnl, commission) = CalculateExitPnl(order);
            _logger.Success($"EXIT FILL (TP): {tradeId} @ {fillPrice} account={accountName} pnl={realizedPnl?.ToString("F2") ?? "n/a"}");
            _network?.SendExitFill(tradeId, fillPrice, "TP", account: accountName, realizedPnl: realizedPnl, commission: commission);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"TP filled @ {fillPrice} PnL={realizedPnl?.ToString("F2") ?? "n/a"}");
            CancelWorkingBracketOrders(tradeId, order.Account);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleCloseFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                tradeId = ExtractTradeIdFromOrderName(order.Name);
            }

            if (string.IsNullOrEmpty(tradeId) || !_orderTracker.TryGetCloseOrder(tradeId, out _))
            {
                _logger.Error($"CRITICAL: Close fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed",
                    $"Close fill for order '{order.Name}' not found in tracking");
                return;
            }

            if (order.OrderState != OrderState.Filled)
            {
                _logger.Warning($"Close {order.Name} state={order.OrderState} ({order.Filled}/{order.Quantity}), waiting for full fill before processing close.");
                return;
            }

            string accountName = order.Account?.Name;
            var (realizedPnl, commission) = CalculateExitPnl(order);
            _logger.Success($"POSITION CLOSED: {tradeId} @ {fillPrice} account={accountName} pnl={realizedPnl?.ToString("F2") ?? "n/a"}");
            _network?.SendExitFill(tradeId, fillPrice, "CLOSE", account: accountName, realizedPnl: realizedPnl, commission: commission);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"Position closed @ {fillPrice} PnL={realizedPnl?.ToString("F2") ?? "n/a"}");
            CancelWorkingBracketOrders(tradeId, order.Account);
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandlePotentialManualClose(Order closeOrder, double fillPrice)
        {
            if (closeOrder?.Instrument == null) return;

            foreach (var tradeId in _orderTracker.GetActiveTradeIds())
            {
                if (!_orderTracker.TryGetEntry(tradeId, out var entryOrder)) continue;
                if (entryOrder.Instrument?.MasterInstrument?.Name != closeOrder.Instrument.MasterInstrument.Name) continue;
                if (entryOrder.OrderState != OrderState.Filled && entryOrder.OrderState != OrderState.PartFilled) continue;
                // Match by account to avoid closing wrong trade in multi-account scenarios
                if (closeOrder.Account != null && entryOrder.Account != null &&
                    closeOrder.Account.Name != entryOrder.Account.Name) continue;

                bool isOpposing = false;
                if (entryOrder.OrderAction == OrderAction.Buy && closeOrder.OrderAction == OrderAction.Sell)
                    isOpposing = true;
                else if (entryOrder.OrderAction == OrderAction.SellShort && closeOrder.OrderAction == OrderAction.BuyToCover)
                    isOpposing = true;

                if (isOpposing)
                {
                    string accountName = closeOrder.Account?.Name;
                    var (realizedPnl, commission) = CalculateExitPnl(closeOrder);
                    _logger.Success($"MANUAL CLOSE DETECTED: {tradeId} @ {fillPrice} via {closeOrder.Name} account={accountName} pnl={realizedPnl?.ToString("F2") ?? "n/a"}");
                    _network?.SendExitFill(tradeId, fillPrice, "CLOSE", account: accountName, realizedPnl: realizedPnl, commission: commission);
                    _network?.SendTradeLog(tradeId, "NT:FILL", $"Manual position closed @ {fillPrice} PnL={realizedPnl?.ToString("F2") ?? "n/a"}");
                    CancelWorkingBracketOrders(tradeId, closeOrder.Account);
                    _orderTracker.RemoveTrade(tradeId);
                    return;
                }
            }
        }

        private (double sl, double tp) CalculateSlTp(double fillPrice, string direction, double slPoints, double rrRatio)
        {
            // Normalize to lowercase to guard against crash recovery returning "Long"/"LONG"
            var dir = direction?.ToLowerInvariant();
            if (dir != "long" && dir != "short")
                throw new ArgumentException($"Invalid direction '{direction}' — must be 'long' or 'short'");

            if (dir == "long")
            {
                return (fillPrice - slPoints, fillPrice + (slPoints * rrRatio));
            }
            else
            {
                return (fillPrice + slPoints, fillPrice - (slPoints * rrRatio));
            }
        }

        /// <summary>
        /// Cancels any working stop-loss or take-profit orders for the given trade.
        /// Called when one side of the bracket fills or the position is closed externally.
        /// </summary>
        private void CancelWorkingBracketOrders(string tradeId, Account account)
        {
            if (account == null)
            {
                // Fallback: try to find account from tracked entry order
                if (_orderTracker.TryGetEntry(tradeId, out var entryOrder))
                {
                    account = ResolveAccountForOrder(entryOrder);
                }
            }
            if (account == null) return;

            if (_orderTracker.TryGetStopLoss(tradeId, out var stopOrder) && IsWorking(stopOrder))
            {
                try
                {
                    _orderTracker.ExpectCancellation(stopOrder.Name);
                    account.Cancel(new[] { stopOrder });
                    _logger.Info($"Cancelled working stop order for {tradeId}");
                }
                catch (Exception ex)
                {
                    _logger.Warning($"Failed to cancel stop order for {tradeId}: {ex.Message}");
                }
            }

            if (_orderTracker.TryGetTakeProfit(tradeId, out var targetOrder) && IsWorking(targetOrder))
            {
                try
                {
                    _orderTracker.ExpectCancellation(targetOrder.Name);
                    account.Cancel(new[] { targetOrder });
                    _logger.Info($"Cancelled working target order for {tradeId}");
                }
                catch (Exception ex)
                {
                    _logger.Warning($"Failed to cancel target order for {tradeId}: {ex.Message}");
                }
            }
        }

        private static bool IsWorking(Order order)
        {
            return order.OrderState == OrderState.Working ||
                   order.OrderState == OrderState.Accepted ||
                   order.OrderState == OrderState.Submitted ||
                   order.OrderState == OrderState.PartFilled;
        }

        private static bool IsEntryOrder(Order order) => 
            order?.Name?.StartsWith("Entry_") == true;
        
        private static bool IsStopOrder(Order order) =>
            (order?.Name?.StartsWith("Stop_") == true) &&
            (order.OrderType == OrderType.StopMarket || order.OrderType == OrderType.StopLimit);
        
        private static bool IsTargetOrder(Order order) =>
            (order?.Name?.StartsWith("Target_") == true) &&
            (order.OrderType == OrderType.Limit);
        
        private static bool IsCloseOrder(Order order) =>
            order?.Name?.StartsWith("Close_") == true;

        // ═══════════════════════════════════════════════════════════════════
        // Tests
        // ═══════════════════════════════════════════════════════════════════

        private async Task TestConnectionAsync()
        {
            _logger.Info("=== TEST CONNECTION ===");
            try
            {
                bool success = await Task.Run(() => _network?.SendTestPingWithResponse(2000) ?? false);
                if (success)
                {
                    _logger.Success("TEST CONNECTION: PASSED - ZMQ REQ/REP working");
                }
                else
                {
                    _logger.Warning("TEST CONNECTION: FAILED - No response from Python");
                }
            }
            catch (Exception ex)
            {
                _logger.Error("TEST CONNECTION: FAILED", ex);
            }
        }

        private async Task RunE2ETestsAsync()
        {
            // E2E tests ALWAYS run in simulate mode — they never submit real orders
            _logger.Info("🧪 E2E tests starting (simulate mode — no real orders will be submitted)");
            E2ETestRunning = true;

            _ui?.SetE2EButtonEnabled(false);
            try
            {
                var runner = new ZmqE2ETestRunner(_network, _logger);
                await runner.RunAllScenariosAsync();
            }
            finally
            {
                E2ETestRunning = false;
                _ui?.SetE2EButtonEnabled(true);
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Utilities
        // ═══════════════════════════════════════════════════════════════════

        private void UpdateStats()
        {
            var (ticks, bars, partials) = _streamingCoordinator?.GetStats() ?? (0, 0, 0);
            var stats = $"Ticks: {ticks} | Bars: {bars + _barsSent} | Partial: {partials} | Cmds: {_commandsReceived}";
            _ui?.UpdateStatus(_connected, stats);
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

        private static double ToUnixSeconds(DateTime dt) =>
            (dt.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

        private static MenuItem FindMenuItem(System.Collections.IEnumerable items, string header)
        {
            if (items == null) return null;
            foreach (var item in items)
            {
                if (item is MenuItem mi)
                {
                    if (mi.Header?.ToString() == header) return mi;
                    var found = FindMenuItem(mi.Items, header);
                    if (found != null) return found;
                }
            }
            return null;
        }
    }
}
