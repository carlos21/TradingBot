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
// Installation:
//   1. Download NetMQ.dll and Newtonsoft.Json.dll
//   2. Copy DLLs to: Documents\NinjaTrader 8\bin\Custom\
//   3. In NinjaTrader: Tools > Edit NinjaScript > right-click References > Add...
//   4. Add references to: NetMQ.dll, Newtonsoft.Json.dll
//   5. Right-click AddOns > New > AddOn, paste this code, compile (F5)
//   6. Click New > TradingBot ZMQ Connector in the Control Center

#region Using declarations
using System;
using System.Collections.Generic;
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

        // Background threads
        private Thread _commandThread;
        private Thread _heartbeatThread;
        private CancellationTokenSource _cts;

        // State
        private volatile bool _connected;
        private Account _account;

        // Stats
        private long _commandsReceived = 0;
        private long _ticksSent = 0;
        private long _barsSent = 0;

        // Duplicate command detection (track processed seq_nums)
        private readonly HashSet<int> _processedSeqNums = new HashSet<int>();
        private readonly object _seqNumLock = new object();
        private const int MAX_TRACKED_SEQ_NUMS = 1000;  // Prevent memory growth

        // UI
        private MenuItem _menuItem;
        private MenuItem _existingNewMenu;

        public TradingBotZmqConnector()
        {
            // Dependency injection - could be replaced with DI container
            _config = new ZmqConfiguration(
                host: "127.0.0.1",
                marketPort: 5555,
                commandPort: 5556,
                queryPort: 5557,
                heartbeatPort: 5558,
                instrument: "MNQ 06-26",
                historyDays: 30,
                batchSize: 500,
                platformVersion: "2.0.0-refactored"
            );
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

            _menuItem = new MenuItem { Header = "TradingBot ZMQ Connector" };
            _menuItem.Click += OnMenuItemClick;
            _existingNewMenu.Items.Add(_menuItem);
        }

        protected override void OnWindowDestroyed(Window window)
        {
            if (_menuItem != null && window is ControlCenter)
            {
                _existingNewMenu?.Items.Remove(_menuItem);
                _menuItem.Click -= OnMenuItemClick;
                _menuItem = null;
                _existingNewMenu = null;
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
                _network = new ZmqNetwork(_config, new JsonMessageSerializer(), _logger);
                _dispatcher = CreateCommandDispatcher();

                _network.Start();
                _cts = new CancellationTokenSource();
                _connected = true;

                // Wait for ZMQ sockets to fully establish (slow joiner protection)
                // This ensures Python's SUB sockets are ready before we send messages
                Thread.Sleep(300);

                // Query config from Python (account name, etc.)
                string configuredAccount = _network.QueryConfig("account");
                if (!string.IsNullOrEmpty(configuredAccount))
                {
                    _logger.Info($"Python specified account: {configuredAccount}");
                }

                // Send connect handshake (reporting what account we'll use)
                _network.SendConnect("ninjatrader", _config.PlatformVersion, account: configuredAccount, pair: _config.Instrument.Split(' ')[0]);
                _logger.Success("Connected to Python TradingBot via ZeroMQ");

                // Start background threads
                _commandThread = new Thread(CommandLoop) { IsBackground = true, Name = "ZMQ-Commands" };
                _commandThread.Start();

                _heartbeatThread = new Thread(HeartbeatLoop) { IsBackground = true, Name = "ZMQ-Heartbeat" };
                _heartbeatThread.Start();

                // Initialize NinjaTrader integrations (using account from Python if specified)
                InitializeAccount(configuredAccount);
                
                // Restore order tracking from broker after potential crash
                _orderTracker.RestoreFromBrokerOrders(_account, _logger);
                
                // Report actual broker positions to Python (broker is source of truth)
                ReportPositionsToPython();
                
                SubscribeToMarketData();

                // Send historical data
                _ = SendHistoryAsync();

                UpdateStats();
            }
            catch (Exception ex)
            {
                _logger.Error("Connection error", ex);
                _network?.SendError("ninjatrader", "connection_failed", ex.Message, FormatExceptionDetails(ex));
                Disconnect("connection error");
            }
        }

        private void Disconnect(string reason = null)
        {
            if (reason != null)
            {
                _logger?.Info($"Disconnecting: {reason}");
            }
            _connected = false;
            _cts?.Cancel();

            UnsubscribeFromMarketData();
            UninitializeAccount();

            _network?.Dispose();
            _network = null;

            _orderTracker?.Clear();
            _orderTracker = null;

            _logger?.Info("Disconnected from Python TradingBot");
            UpdateStats();
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
                        ["quantity"] = entryOrder.Quantity,
                        ["order_state"] = entryOrder.OrderState.ToString(),
                    };

                    if (stopOrder != null)
                        position["stop_loss"] = stopOrder.StopPrice;
                    if (targetOrder != null)
                        position["take_profit"] = targetOrder.LimitPrice;

                    positions.Add(position);
                }

                // Also report any untracked working orders (orphan detection)
                var untrackedOrders = new JArray();
                foreach (var order in _account?.Orders ?? System.Linq.Enumerable.Empty<Order>())
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
                        });
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
            dispatcher.Register(new OrderOpenHandler(_network, _logger, _account, _config.Instrument, _orderTracker));
            dispatcher.Register(new OrderCloseHandler(_network, _logger, _account, _config.Instrument, _orderTracker));
            dispatcher.Register(new OrderModifyHandler(_network, _logger, _account, _orderTracker));
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
                try
                {
                    var envelope = _network?.ReceiveCommand(timeoutMs: 100);
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
                    string tradeId = null;
                    try { tradeId = envelope.Payload?["trade_id"]?.ToString(); } catch { }

                    try
                    {
                        _dispatcher.Dispatch(envelope);
                        // Send success ack
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, true, tradeId);
                    }
                    catch (Exception dispatchEx)
                    {
                        _logger.Error($"Command dispatch failed: {envelope.MsgType}", dispatchEx);
                        // Send failure ack
                        _network?.SendCommandAck(envelope.MsgType, envelope.SeqNum, false, tradeId, dispatchEx.Message);
                        _network?.SendError("ninjatrader", "command_dispatch_failed", $"{envelope.MsgType}: {dispatchEx.Message}");
                    }

                    if (_commandsReceived % 10 == 0) UpdateStats();
                }
                catch (Exception ex)
                {
                    _logger.Error("Command loop error", ex);
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

                // Prevent unbounded growth - remove oldest if too many
                if (_processedSeqNums.Count > MAX_TRACKED_SEQ_NUMS)
                {
                    // Simple approach: clear half the set when limit reached
                    // In production, use a circular buffer or LRU cache
                    var toRemove = new List<int>();
                    int count = 0;
                    foreach (var num in _processedSeqNums)
                    {
                        if (count++ < MAX_TRACKED_SEQ_NUMS / 2)
                            toRemove.Add(num);
                        else
                            break;
                    }
                    foreach (var num in toRemove)
                        _processedSeqNums.Remove(num);
                }

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
            var instrument = Instrument.GetInstrument(_config.Instrument);
            if (instrument == null)
            {
                _logger.Error($"Cannot subscribe, instrument '{_config.Instrument}' not found");
                return;
            }
            instrument.MarketData.Update += OnMarketDataUpdate;
            _logger.Info($"Subscribed to market data for {_config.Instrument}");
        }

        private void UnsubscribeFromMarketData()
        {
            var instrument = Instrument.GetInstrument(_config.Instrument);
            if (instrument != null)
            {
                instrument.MarketData.Update -= OnMarketDataUpdate;
                _logger.Info($"Unsubscribed from market data for {_config.Instrument}");
            }
        }

        private void OnMarketDataUpdate(object sender, MarketDataEventArgs e)
        {
            try
            {
                if (!_connected || e.MarketDataType != MarketDataType.Last) return;

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
                var barsRequest = new BarsRequest(instrument, DateTime.UtcNow.AddDays(-days), DateTime.UtcNow)
                {
                    BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 }
                };

                var batch = new List<JObject>();
                int count = 0;

                barsRequest.Request((bars, errorCode, errorMessage) =>
                {
                    if (errorCode != ErrorCode.NoError)
                    {
                        _logger.Error($"BarsRequest failed: {errorMessage}");
                        tcs.SetResult(false);
                        return;
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
                    _network?.SendHistoryEnd();
                    tcs.SetResult(true);
                });

                await tcs.Task;
            }
            catch (Exception ex)
            {
                _logger.Error("SendHistory error", ex);
                _network?.SendError("ninjatrader", "history_load_failed", $"Failed to load {days} days of history", FormatExceptionDetails(ex));
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Account / Order Management
        // ═══════════════════════════════════════════════════════════════════

        private void InitializeAccount(string preferredAccount = null)
        {
            try
            {
                if (Account.All.Count == 0)
                {
                    _logger.Warning("No trading accounts found");
                    _network?.SendError("ninjatrader", "no_account", "No trading accounts found");
                    return;
                }

                // Use preferred account name if specified (from Python), otherwise use first available
                if (!string.IsNullOrEmpty(preferredAccount))
                {
                    _account = Account.All.FirstOrDefault(a => a.Name == preferredAccount);
                    if (_account == null)
                    {
                        _logger.Warning($"Python-specified account '{preferredAccount}' not found, using first available");
                        _account = Account.All[0];
                    }
                    else
                    {
                        _logger.Info($"Using Python-specified account: {_account.Name}");
                    }
                }
                else
                {
                    _account = Account.All[0];
                    _logger.Info($"Using account: {_account.Name}");
                }
                
                _account.ExecutionUpdate += OnExecutionUpdate;
                _account.OrderUpdate += OnOrderUpdate;
            }
            catch (Exception ex)
            {
                _logger.Error("Account initialization failed", ex);
                _network?.SendError("ninjatrader", "account_init_failed", ex.Message, FormatExceptionDetails(ex));
            }
        }

        private void UninitializeAccount()
        {
            if (_account != null)
            {
                _account.ExecutionUpdate -= OnExecutionUpdate;
                _account.OrderUpdate -= OnOrderUpdate;
                _account = null;
            }
        }

        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            try
            {
                var order = e.Order;
                _logger.Info($"ORDER UPDATE: {order.Name} state={order.OrderState}");

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

                // Notify Python of rejected/cancelled orders
                if (order.OrderState == OrderState.Rejected || order.OrderState == OrderState.Cancelled)
                {
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
            
            return null;
        }

        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            try
            {
                var execution = e.Execution;
                var order = execution.Order;
                var fillPrice = execution.Price;

                _logger.Info($"EXECUTION: {order.Name} @ {fillPrice} qty={execution.Quantity}");
                _network?.SendTradeLog(order.Name, "NT:EXECUTION", $"Execution: {execution.Quantity} @ {fillPrice}");

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
            }
            catch (Exception ex)
            {
                _logger.Error("Execution update error", ex);
            }
        }

        private void HandleEntryFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId) ||
                !_orderTracker.TryGetPendingEntry(tradeId, out var entry))
            {
                _logger.Error($"CRITICAL: Entry fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed", 
                    $"Entry fill for order '{order.Name}' not found in tracking");
                return;
            }

            var (sl, tp) = CalculateSlTp(fillPrice, entry.Direction, entry.SlPoints, entry.RrRatio);

            _logger.Success($"ENTRY FILL: {tradeId} @ {fillPrice} SL={sl} TP={tp}");
            _network?.SendEntryFill(tradeId, fillPrice, sl, tp);
            _network?.SendTradeLog(tradeId, "NT:FILL", $"Entry filled @ {fillPrice}");
        }

        private void HandleStopLossFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                _logger.Error($"CRITICAL: SL fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed", 
                    $"SL fill for order '{order.Name}' not found in tracking");
                return;
            }

            _logger.Warning($"EXIT FILL (SL): {tradeId} @ {fillPrice}");
            _network?.SendExitFill(tradeId, fillPrice, "SL");
            _network?.SendTradeLog(tradeId, "NT:FILL", $"SL filled @ {fillPrice}");
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleTakeProfitFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                _logger.Error($"CRITICAL: TP fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed", 
                    $"TP fill for order '{order.Name}' not found in tracking");
                return;
            }

            _logger.Success($"EXIT FILL (TP): {tradeId} @ {fillPrice}");
            _network?.SendExitFill(tradeId, fillPrice, "TP");
            _network?.SendTradeLog(tradeId, "NT:FILL", $"TP filled @ {fillPrice}");
            _orderTracker.RemoveTrade(tradeId);
        }

        private void HandleCloseFill(Order order, double fillPrice)
        {
            if (!_orderTracker.TryGetTradeIdForOrder(order, out var tradeId))
            {
                _logger.Error($"CRITICAL: Close fill for order '{order.Name}' not found in tracking! Cannot process fill.");
                _network?.SendError("ninjatrader", "fill_tracking_failed", 
                    $"Close fill for order '{order.Name}' not found in tracking");
                return;
            }

            _logger.Success($"POSITION CLOSED: {tradeId} @ {fillPrice}");
            _network?.SendExitFill(tradeId, fillPrice, "CLOSE");
            _network?.SendTradeLog(tradeId, "NT:FILL", $"Position closed @ {fillPrice}");
            _orderTracker.RemoveTrade(tradeId);
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

        private static bool IsEntryOrder(Order order) => 
            order.Name == "Entry" || (order.Name != null && order.Name.StartsWith("Entry_"));
        
        private static bool IsStopOrder(Order order) =>
            ((order.Name == "Stop" || order.Name.StartsWith("Stop_")) &&
            (order.OrderType == OrderType.StopMarket || order.OrderType == OrderType.StopLimit));
        
        private static bool IsTargetOrder(Order order) =>
            ((order.Name == "Target" || order.Name.StartsWith("Target_")) &&
            (order.OrderType == OrderType.Limit || order.OrderType == OrderType.Market));
        
        private static bool IsCloseOrder(Order order) =>
            order.Name != null && order.Name.StartsWith("Close_");

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
            _ui?.SetE2EButtonEnabled(false);
            try
            {
                var runner = new ZmqE2ETestRunner(_network, _logger);
                await runner.RunAllScenariosAsync();
            }
            finally
            {
                _ui?.SetE2EButtonEnabled(true);
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Utilities
        // ═══════════════════════════════════════════════════════════════════

        private void UpdateStats()
        {
            var stats = $"Ticks: {_ticksSent} | Bars: {_barsSent} | Cmds: {_commandsReceived}";
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

        private static long ToUnixSeconds(DateTime dt) =>
            (long)(dt.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds;

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
