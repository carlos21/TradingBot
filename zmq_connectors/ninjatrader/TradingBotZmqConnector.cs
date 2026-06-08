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
        private TickRateLimiter _tickRateLimiter;

        // Background threads
        private Thread _commandThread;
        private Thread _heartbeatThread;
        private CancellationTokenSource _cts;

        // State
        private volatile bool _connected;
        private Instrument _subscribedInstrument;

        // When true, ALL command handlers force simulate mode (no real orders)
        internal static volatile bool E2ETestRunning = false;

        // Stats
        private long _commandsReceived = 0;
        private long _ticksSent = 0;
        private long _barsSent = 0;
        private long _partialBarsSent = 0;

        // Live bar streaming
        private BarsRequest _liveBarsRequest;
        private DateTime _lastSentBarTime = DateTime.MinValue;
        private DateTime _lastFormingBarTime = DateTime.MinValue;
        private DateTime _lastHistoryBarTime = DateTime.MinValue;
        private readonly object _barSendLock = new object();
        private TickRateLimiter _partialBarRateLimiter;
        private System.Timers.Timer _liveBarsDelayTimer;  // Fallback: creates BarsRequest if no tick arrives within 10s
        private volatile bool _liveBarsSubscribed;
        private System.Timers.Timer _barsRequestWatchdog; // Recreates BarsRequest if completed bars stall

        // Market status tracking (suppress duplicate warnings when market is closed)
        private bool _marketIsOpen = true;
        private DateTime _lastMarketStatusSent = DateTime.MinValue;
        private SessionIterator _sessionIterator;

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
                _tickRateLimiter = new TickRateLimiter(_config.MaxTicksPerSecond);
                _partialBarRateLimiter = new TickRateLimiter(1); // 1 partial bar per second
                _network = new ZmqNetwork(_config, new JsonMessageSerializer(_logger), _logger);

                _network.Start();
                _cts = new CancellationTokenSource();
                _connected = true;

                // Wait for ZMQ sockets to fully establish (slow joiner protection)
                // This ensures Python's SUB sockets are ready before we send messages
                Thread.Sleep(300);

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

                // Send connect handshake (minimal — account name is not needed)
                _network.SendConnect("ninjatrader", _config.PlatformVersion, pair: _config.Instrument.Split(' ')[0]);
                _logger.Success("Connected to Python TradingBot via ZeroMQ");

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
                
                SubscribeToMarketData();
                // BarsRequest is created on first tick (or 10s timer fallback)
                // to ensure NinjaTrader's data connection is fully established.
                StartLiveBarsDelayTimer();

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

                // Stop market-data thread BEFORE tearing down ZMQ sockets
                UnsubscribeFromLiveBars();
                UnsubscribeFromMarketData();

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

                // Reset bar streaming state so reconnect starts fresh
                _lastSentBarTime = DateTime.MinValue;
                _lastFormingBarTime = DateTime.MinValue;
                _lastHistoryBarTime = DateTime.MinValue;

                // Reset stats
                _commandsReceived = 0;
                _ticksSent = 0;
                _barsSent = 0;
                _partialBarsSent = 0;

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
            dispatcher.Register(new OrderOpenHandler(_network, _logger, _config.Instrument, _orderTracker, simulate));
            dispatcher.Register(new OrderCloseHandler(_network, _logger, _config.Instrument, _orderTracker, simulate));
            dispatcher.Register(new OrderModifyHandler(_network, _logger, _orderTracker, simulate));
            dispatcher.Register(new RefreshRequestHandler(_network, _logger, SendHistoryAsync));
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
        // Market Data
        // ═══════════════════════════════════════════════════════════════════

        private void SubscribeToMarketData()
        {
            _subscribedInstrument = Instrument.GetInstrument(_config.Instrument);
            if (_subscribedInstrument == null)
            {
                _logger.Error($"Cannot subscribe, instrument '{_config.Instrument}' not found");
                return;
            }
            _subscribedInstrument.MarketData.Update += OnMarketDataUpdate;
            _logger.Info($"Subscribed to market data for {_config.Instrument}");
        }

        private void UnsubscribeFromMarketData()
        {
            if (_subscribedInstrument != null)
            {
                _subscribedInstrument.MarketData.Update -= OnMarketDataUpdate;
                _logger.Info($"Unsubscribed from market data for {_config.Instrument}");
                _subscribedInstrument = null;
            }
        }

        private void SubscribeToLiveBars()
        {
            if (_subscribedInstrument == null)
            {
                _logger.Error("Cannot subscribe to live bars, instrument is null");
                return;
            }
            if (_liveBarsSubscribed)
            {
                return;
            }
            _liveBarsSubscribed = true;

            // Use barsBack overload to avoid cached-data issues with DateTime range.
            // 2 bars is enough to establish the _lastSentBarTime baseline.
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
                            var pair = _config.Instrument.Split(' ')[0];
                            // Send all completed bars from the initial load so they don't get
                            // lost when the catch-up logic skips them based on _lastSentBarTime.
                            // The forming bar (last index) is excluded — it will be sent when it completes.
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

                            int idx = Math.Max(0, bars.Bars.Count - 2);
                            _lastSentBarTime = bars.Bars.GetTime(idx);
                            _lastFormingBarTime = bars.Bars.GetTime(bars.Bars.Count - 1);
                        }
                        _sessionIterator = new SessionIterator(bars.Bars);
                        _logger.Info($"Live bars stream ready. Cached {bars.Bars.Count} bars, lastCompleted={_lastSentBarTime:HH:mm:ss}, sent {Math.Max(0, bars.Bars.Count - 1)} initial bar(s)");
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

        private void StartLiveBarsDelayTimer()
        {
            StopLiveBarsDelayTimer();
            _liveBarsDelayTimer = new System.Timers.Timer(10000);
            _liveBarsDelayTimer.Elapsed += (s, e) =>
            {
                StopLiveBarsDelayTimer();
                if (!_liveBarsSubscribed && _connected)
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
            _barsRequestWatchdog = new System.Timers.Timer(30000); // Check every 30s
            _barsRequestWatchdog.Elapsed += OnBarsRequestWatchdogTick;
            _barsRequestWatchdog.AutoReset = true;
            _barsRequestWatchdog.Start();
        }

        private void StopBarsRequestWatchdog()
        {
            if (_barsRequestWatchdog != null)
            {
                _barsRequestWatchdog.Stop();
                _barsRequestWatchdog.Elapsed -= OnBarsRequestWatchdogTick;
                _barsRequestWatchdog.Dispose();
                _barsRequestWatchdog = null;
            }
        }

        private void OnBarsRequestWatchdogTick(object sender, System.Timers.ElapsedEventArgs e)
        {
            try
            {
                if (!_connected || !_liveBarsSubscribed) return;

                // Check market status every watchdog tick (30s) and notify Python
                CheckMarketStatus();

                if (_lastSentBarTime == DateTime.MinValue) return;

                var elapsed = DateTime.Now - _lastSentBarTime;
                if (elapsed.TotalSeconds > 75)
                {
                    _logger.Warning($"[BarsRequestWatchdog] No completed bar sent in {elapsed.TotalSeconds:F0}s (threshold=75s). Recreating BarsRequest...");
                    UnsubscribeFromLiveBars();
                    SubscribeToLiveBars();
                }
            }
            catch (Exception ex)
            {
                _logger.Error("[BarsRequestWatchdog] Error in watchdog tick", ex);
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
                var pair = _config.Instrument.Split(' ')[0];
                _network?.SendMarketStatus(_marketIsOpen, nextBegin, pair);
                var status = _marketIsOpen ? "OPEN" : "CLOSED";
                _logger.Info($"[MarketStatus] {pair} market is {status}, next_open={nextBegin:yyyy-MM-dd HH:mm:ss}");
            }
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
                _liveBarsSubscribed = false;
                _sessionIterator = null;
                _logger.Info("Unsubscribed from live 1m bars");
            }
        }

        private void OnLiveBarsUpdate(object sender, BarsUpdateEventArgs e)
        {
            try
            {
                if (!_connected) return;

                var series = e.BarsSeries;
                if (series == null || series.Count == 0) return;

                var formingBarTime = series.GetTime(series.Count - 1);
                var pair = _config.Instrument.Split(' ')[0];

                // When the forming bar time advances, send ALL bars that closed since last update.
                // This handles bursts after UI thread lag, market halts, or data provider reconnects.
                lock (_barSendLock)
                {
                    if (_lastFormingBarTime != DateTime.MinValue && formingBarTime > _lastFormingBarTime)
                    {
                        int sent = 0;
                        int skipped = 0;
                        // Walk backwards from the bar before the forming bar
                        for (int i = series.Count - 2; i >= 0; i--)
                        {
                            var barTime = series.GetTime(i);

                            // Stop once we reach already-sent bars
                            if (barTime <= _lastSentBarTime)
                            {
                                skipped++;
                                break;
                            }

                            // Stop once we pass the previous forming bar (safety)
                            if (barTime < _lastFormingBarTime)
                            {
                                skipped++;
                                break;
                            }

                            var open   = series.GetOpen(i);
                            var high   = series.GetHigh(i);
                            var low    = series.GetLow(i);
                            var close  = series.GetClose(i);
                            var volume = (long)series.GetVolume(i);

                            _network?.SendBar(pair, barTime, open, high, low, close, volume, isPartial: false);
                            _barsSent++;
                            _lastSentBarTime = barTime;
                            sent++;
                        }
                        if (sent > 1 || skipped > 0)
                            _logger.Info($"[CatchUp] sent={sent} skipped={skipped} forming={formingBarTime:HH:mm:ss} lastForming={_lastFormingBarTime:HH:mm:ss} lastSent={_lastSentBarTime:HH:mm:ss} seriesCount={series.Count}");
                    }

                    _lastFormingBarTime = formingBarTime;
                }

                // Process updates in the notified range (typically just the forming bar)
                for (int i = e.MinIndex; i <= e.MaxIndex; i++)
                {
                    bool isFormingBar = (i == series.Count - 1);
                    if (!isFormingBar)
                    {
                        var barTime = series.GetTime(i);
                        lock (_barSendLock)
                        {
                            if (barTime <= _lastSentBarTime) continue;
                        }

                        var open   = series.GetOpen(i);
                        var high   = series.GetHigh(i);
                        var low    = series.GetLow(i);
                        var close  = series.GetClose(i);
                        var volume = (long)series.GetVolume(i);

                        _network?.SendBar(pair, barTime, open, high, low, close, volume, isPartial: false);
                        _barsSent++;
                        lock (_barSendLock) { _lastSentBarTime = barTime; }
                    }
                    else if (_partialBarRateLimiter?.TryAllow() == true)
                    {
                        var open   = series.GetOpen(i);
                        var high   = series.GetHigh(i);
                        var low    = series.GetLow(i);
                        var close  = series.GetClose(i);
                        var volume = (long)series.GetVolume(i);

                        _network?.SendBar(pair, formingBarTime, open, high, low, close, volume, isPartial: true);
                        _partialBarsSent++;
                    }
                }

                if ((_barsSent + _partialBarsSent) % 100 == 0) UpdateStats();
            }
            catch (Exception ex)
            {
                _logger.Error("Live bars update error", ex);
                _network?.SendError("ninjatrader", "live_bar_error", ex.Message, FormatExceptionDetails(ex));
            }
        }

        private void OnMarketDataUpdate(object sender, MarketDataEventArgs e)
        {
            try
            {
                if (!_connected || e.MarketDataType != MarketDataType.Last) return;

                // Create BarsRequest on the first tick after connect.
                // This ensures NinjaTrader's data connection is fully established
                // before we request bars, which prevents the Update event from stalling.
                if (!_liveBarsSubscribed)
                {
                    StopLiveBarsDelayTimer();
                    SubscribeToLiveBars();
                }

                if (!_tickRateLimiter.TryAllow()) return;

                _network?.SendTick(
                    e.Instrument.MasterInstrument.Name,
                    e.Price,
                    (long)e.Volume,
                    e.Time);

                _ticksSent++;
                if (_ticksSent % 500 == 0) UpdateStats();
            }
            catch (Exception ex)
            {
                _logger.Error("Market data error", ex);
                _network?.SendError("ninjatrader", "market_data_error", ex.Message);
            }
        }

        private async Task SendHistoryAsync(int days = 30)
        {
            try
            {
                var instrument = Instrument.GetInstrument(_config.Instrument);
                if (instrument == null)
                {
                    _logger.Error($"Instrument '{_config.Instrument}' not found");
                    return;
                }

                var tcs = new TaskCompletionSource<bool>();
                // IMPORTANT: DateTime-range BarsRequest forces NinjaTrader to load fresh data
                // from the data provider if the range is not fully cached. The barsBack overload
                // reads from cache only and can return stale data on startup before the cache
                // has been warmed by real-time ticks. We use DateTime range to guarantee fresh
                // data on every refresh, even though NT may cache the result internally.
                var startDateTime = DateTime.UtcNow.AddDays(-days);
                var endDateTime = DateTime.UtcNow;
                _logger.Info($"[History] DateTime range | days={days} | start={startDateTime:yyyy-MM-dd HH:mm:ss} UTC | end={endDateTime:yyyy-MM-dd HH:mm:ss} UTC | requesting...");
                var barsRequest = new BarsRequest(instrument, startDateTime, endDateTime)
                {
                    BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                    TradingHours = TradingHours.Get("Default 24 x 7")
                };

                // Notify Python that a refresh is starting so it buffers live bars
                _network?.SendRefreshStart();
                _logger.Info("Sending refresh_start");

                try
                {
                    var batch = new List<JObject>();
                    int count = 0;

                    barsRequest.Request((bars, errorCode, errorMessage) =>
                    {
                        try
                        {
                            if (errorCode != ErrorCode.NoError)
                            {
                                _logger.Error($"BarsRequest failed: {errorMessage}");
                                tcs.TrySetResult(false);
                                return;
                            }

                            if (bars?.Bars == null)
                            {
                                _logger.Error("BarsRequest returned null bars");
                                tcs.TrySetResult(false);
                                return;
                            }

                            int receivedCount = bars.Bars.Count;
                            if (receivedCount > 0)
                            {
                                var firstTime = bars.Bars.GetTime(0);
                                var lastTime = bars.Bars.GetTime(receivedCount - 1);
                                _lastHistoryBarTime = lastTime;
                                var gapToNow = DateTime.Now - lastTime;
                                _logger.Info($"[History] BarsRequest returned {receivedCount} bars | first={firstTime:yyyy-MM-dd HH:mm:ss} | last={lastTime:yyyy-MM-dd HH:mm:ss} | gapToNow={gapToNow.TotalSeconds:F0}s");
                            }
                            else
                            {
                                _logger.Warning("[History] BarsRequest returned 0 bars");
                            }

                            for (int i = 0; i < bars.Bars.Count; i++)
                            {
                                batch.Add(new JObject
                                {
                                    ["time"] = ToUnixSeconds(bars.Bars.GetTime(i)),
                                    ["open"] = bars.Bars.GetOpen(i),
                                    ["high"] = bars.Bars.GetHigh(i),
                                    ["low"] = bars.Bars.GetLow(i),
                                    ["close"] = bars.Bars.GetClose(i),
                                    ["volume"] = (long)bars.Bars.GetVolume(i),
                                    ["pair"] = _config.Instrument.Split(' ')[0]
                                });
                                count++;

                                if (batch.Count >= _config.BatchSize)
                                {
                                    _network?.SendHistoryBatch(_config.Instrument.Split(' ')[0], batch, days);
                                    batch.Clear();
                                }
                            }

                            if (batch.Count > 0) _network?.SendHistoryBatch(_config.Instrument.Split(' ')[0], batch, days);

                            _barsSent = count;
                            _logger.Info($"Sent {count} historical bars ({days} days)");

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

                            _network?.SendHistoryEnd();
                            tcs.TrySetResult(true);
                        }
                        catch (Exception callbackEx)
                        {
                            _logger.Error("BarsRequest callback error", callbackEx);
                            tcs.TrySetResult(false);
                        }
                    });

                    var timeoutTask = Task.Delay(TimeSpan.FromSeconds(30));
                    var completedTask = await Task.WhenAny(tcs.Task, timeoutTask);
                    if (completedTask == timeoutTask)
                    {
                        _logger.Error("BarsRequest timed out after 30 seconds");
                        _network?.SendError("ninjatrader", "history_timeout", "BarsRequest timed out after 30 seconds");
                    }
                }
                finally
                {
                    barsRequest?.Dispose();
                }
            }
            catch (Exception ex)
            {
                _logger.Error("SendHistory error", ex);
                _network?.SendError("ninjatrader", "history_load_failed", $"Failed to load {days} days of history", FormatExceptionDetails(ex));
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
            var pair = _config.Instrument.Split(' ')[0];

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
                    if (gapBatch.Count > 0)
                    {
                        _network?.SendHistoryBatch(pair, gapBatch, days: 1);
                        _barsSent += gapCount;
                        _logger.Success($"[GapFill] SUCCESS — sent {gapCount} bars on attempt {attempt}/{maxAttempts}");
                    }

                    // If the gap-fill didn't reach the end, update gapStart for next attempt
                    if (gapLast.HasValue && gapLast.Value < gapEnd.AddMinutes(-1))
                    {
                        gapStart = gapLast.Value;
                        _logger.Info($"[GapFill] Gap partially filled — continuing from {gapStart:yyyy-MM-dd HH:mm:ss} UTC");
                        continue; // retry with updated gapStart
                    }

                    return; // gap fully filled
                }

                _logger.Warning($"[GapFill] Attempt {attempt}/{maxAttempts} | no new bars received");

                if (attempt < maxAttempts)
                {
                    await Task.Delay(TimeSpan.FromSeconds(2)); // brief delay before retry
                }
            }

            _logger.Error($"[GapFill] FAILED after {maxAttempts} attempts — gap remains from {gapStart:yyyy-MM-dd HH:mm:ss} UTC to {gapEnd:yyyy-MM-dd HH:mm:ss} UTC");
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

                    if (!string.IsNullOrEmpty(tid) && _orderTracker.TryGetPendingModify(tid, out var modInfo))
                    {
                        if (!wasExpected)
                        {
                            _logger.Warning($"Order {order.Name} was cancelled unexpectedly (not by modify/close workflow). Discarding pending modify.");
                            _orderTracker.RemovePendingModify(tid);
                        }
                        else if (!_orderTracker.TryGetEntry(tid, out _))
                        {
                            _logger.Warning($"Order {order.Name} cancelled but trade {tid} no longer active. Discarding pending modify.");
                            _orderTracker.RemovePendingModify(tid);
                        }
                        else
                        {
                            try
                            {
                                var account = ResolveAccountForOrder(order);
                                if (account == null)
                                {
                                    _logger.Error($"Cannot create replacement order for {tid}: account not found");
                                    _orderTracker.RemovePendingModify(tid);
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
                                    _orderTracker.RemovePendingModify(tid);
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
                                    _orderTracker.RemovePendingModify(tid);
                                    _logger.Error($"Failed to create replacement order for {tid}");
                                    _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to create replacement for {tid}");
                                }
                            }
                            catch (Exception modEx)
                            {
                                _orderTracker.RemovePendingModify(tid);
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
                _logger.Error($"CRITICAL: Entry fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed",
                    $"Entry fill for order '{order.Name}' not found in tracking");
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

            string accountName = order.Account?.Name;
            _logger.Warning($"EXIT FILL (SL): {tradeId} @ {fillPrice} account={accountName}");
            _network?.SendExitFill(tradeId, fillPrice, "SL", account: accountName);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"SL filled @ {fillPrice}");
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

            string accountName = order.Account?.Name;
            _logger.Success($"EXIT FILL (TP): {tradeId} @ {fillPrice} account={accountName}");
            _network?.SendExitFill(tradeId, fillPrice, "TP", account: accountName);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"TP filled @ {fillPrice}");
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

            string accountName = order.Account?.Name;
            _logger.Success($"POSITION CLOSED: {tradeId} @ {fillPrice} account={accountName}");
            _network?.SendExitFill(tradeId, fillPrice, "CLOSE", account: accountName);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"Position closed @ {fillPrice}");
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

                bool isOpposing = false;
                if (entryOrder.OrderAction == OrderAction.Buy && closeOrder.OrderAction == OrderAction.Sell)
                    isOpposing = true;
                else if (entryOrder.OrderAction == OrderAction.SellShort && closeOrder.OrderAction == OrderAction.BuyToCover)
                    isOpposing = true;

                if (isOpposing)
                {
                    string accountName = closeOrder.Account?.Name;
                    _logger.Success($"MANUAL CLOSE DETECTED: {tradeId} @ {fillPrice} via {closeOrder.Name} account={accountName}");
                    _network?.SendExitFill(tradeId, fillPrice, "CLOSE", account: accountName);
                    _network?.SendTradeLog(tradeId, "NT:FILL", $"Manual position closed @ {fillPrice}");
                    CancelWorkingBracketOrders(tradeId, closeOrder.Account);
                    _orderTracker.RemoveTrade(tradeId);
                    return;
                }
            }
        }

        private (double sl, double tp) CalculateSlTp(double fillPrice, string direction, double slPoints, double rrRatio)
        {
            if (direction == "long")
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
            var stats = $"Ticks: {_ticksSent} | Bars: {_barsSent} | Partial: {_partialBarsSent} | Cmds: {_commandsReceived}";
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
