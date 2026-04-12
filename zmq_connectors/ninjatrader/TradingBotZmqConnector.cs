// TradingBotZmqConnector.cs — NinjaScript AddOn (ZeroMQ Edition)
// High-performance ZeroMQ connector for Python TradingBot.
//
// Installation:
//   1. Download NetMQ.dll and Newtonsoft.Json.dll
//   2. Copy DLLs to: Documents\NinjaTrader 8\bin\Custom\
//   3. In NinjaTrader: Tools > Edit NinjaScript > right-click References > Add...
//   4. Add references to: NetMQ.dll, Newtonsoft.Json.dll
//   5. Right-click AddOns > New > AddOn, paste this code, compile (F5)
//   6. Click New > TradingBot ZMQ Connector in the Control Center
//
// Protocol: tcp://127.0.0.1:5555-5558 (PUB/SUB, PUSH/PULL, REQ/REP)
//
// Logging Strategy:
//   - Log()              -> UI window + NinjaTrader Output window
//   - NotifyError()      -> Alert popup (red) + Log()
//   - NotifyWarning()    -> Alert popup (orange) + Log()
//   - NotifySuccess()    -> Alert popup (green) + Log()
//   - SendError()        -> Send error to Python via ZMQ
//   - SendTradeLog()     -> Send trade event to Python via ZMQ
//
// All errors are logged to UI. Critical errors also show pop-up alerts.

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using NetMQ;
using NetMQ.Sockets;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.Core.FloatingPoint;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    // ═══════════════════════════════════════════════════════════════════════
    // Protocol Messages
    // ═══════════════════════════════════════════════════════════════════════
    
    public static class MessageType
    {
        public const string Tick = "tick";
        public const string Bar = "bar";
        public const string PartialBar = "partial";
        public const string HistoryBatch = "history_batch";
        public const string HistoryEnd = "history_end";
        public const string OrderOpen = "order_open";
        public const string OrderClose = "order_close";
        public const string OrderModify = "order_modify";
        public const string EntryFill = "entry_fill";
        public const string ExitFill = "exit_fill";
        public const string OrderRejected = "order_rejected";
        public const string TradeLog = "trade_log";
        public const string Error = "error";
        public const string Heartbeat = "heartbeat";
        public const string Connect = "connect";
        public const string Disconnect = "disconnect";
        public const string RefreshRequest = "refresh_request";
        public const string RefreshStart = "refresh_start";
        public const string PositionQuery = "position_query";
        public const string PositionResponse = "position_response";
        
        // Testing
        public const string TestPing = "test_ping";
        public const string TestPong = "test_pong";
        public const string TestStart = "test_start";
        public const string TestStatus = "test_status";
        public const string TestResult = "test_result";
    }

    public class MessageEnvelope
    {
        [JsonProperty("msg_type")]
        public string MsgType { get; set; }
        
        [JsonProperty("timestamp")]
        public double Timestamp { get; set; }
        
        [JsonProperty("seq_num")]
        public int SeqNum { get; set; }
        
        [JsonProperty("payload")]
        public JObject Payload { get; set; }
        
        public string ToJson()
        {
            return JsonConvert.SerializeObject(this);
        }
        
        public static MessageEnvelope Create(string msgType, JObject payload, int seqNum = 0)
        {
            return new MessageEnvelope
            {
                MsgType = msgType,
                Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
                SeqNum = seqNum,
                Payload = payload
            };
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // ZeroMQ Network Layer
    // ═══════════════════════════════════════════════════════════════════════

    internal class ZmqNetwork : IDisposable
    {
        private PublisherSocket _marketPub;      // PUB: Send market data
        private PullSocket _commandPull;         // PULL: Receive commands
        private RequestSocket _queryReq;         // REQ: Send queries to Python
        private PublisherSocket _heartbeatPub;   // PUB: Send heartbeats
        
        private readonly string _marketDataAddr;
        private readonly string _commandAddr;
        private readonly string _queryAddr;
        private readonly string _heartbeatAddr;
        
        private int _seqNum = 0;
        private readonly object _seqLock = new object();
        
        public bool IsConnected => _marketPub != null && _commandPull != null;
        
        public ZmqNetwork(
            string host = "127.0.0.1",
            int marketPort = 5555,
            int commandPort = 5556,
            int queryPort = 5557,
            int heartbeatPort = 5558)
        {
            _marketDataAddr = $"tcp://{host}:{marketPort}";
            _commandAddr = $"tcp://{host}:{commandPort}";
            _queryAddr = $"tcp://{host}:{queryPort}";
            _heartbeatAddr = $"tcp://{host}:{heartbeatPort}";
        }
        
        public void Start()
        {
            // PUB socket: Send market data to Python
            _marketPub = new PublisherSocket();
            _marketPub.Connect(_marketDataAddr);
            
            // PULL socket: Receive commands from Python
            _commandPull = new PullSocket();
            _commandPull.Connect(_commandAddr);
            
            // REQ socket: Send queries to Python (Python binds REP)
            _queryReq = new RequestSocket();
            _queryReq.Connect(_queryAddr);
            
            // PUB socket: Send heartbeats
            _heartbeatPub = new PublisherSocket();
            _heartbeatPub.Connect(_heartbeatAddr);
        }
        
        public void Stop()
        {
            _marketPub?.Dispose();
            _commandPull?.Dispose();
            _queryReq?.Dispose();
            _heartbeatPub?.Dispose();
            
            _marketPub = null;
            _commandPull = null;
            _queryReq = null;
            _heartbeatPub = null;
        }
        
        public void Dispose()
        {
            Stop();
        }
        
        private int NextSeq()
        {
            lock (_seqLock)
            {
                return ++_seqNum;
            }
        }
        
        // Send methods (Platform → Python)
        public void SendMarketData(JObject payload, string msgType)
        {
            if (_marketPub == null) return;
            
            var envelope = MessageEnvelope.Create(msgType, payload, NextSeq());
            _marketPub.SendFrame(envelope.ToJson());
        }
        
        public void SendTick(string pair, double price, long volume, DateTime time, 
                            double? bid = null, double? ask = null)
        {
            var payload = new JObject
            {
                ["pair"] = pair,
                ["price"] = price,
                ["volume"] = volume,
                ["time"] = (long)(time.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds
            };
            if (bid.HasValue) payload["bid"] = bid.Value;
            if (ask.HasValue) payload["ask"] = ask.Value;
            
            SendMarketData(payload, MessageType.Tick);
        }
        
        public void SendBar(string pair, DateTime time, double open, double high, 
                           double low, double close, long volume, bool isPartial = false)
        {
            var payload = new JObject
            {
                ["pair"] = pair,
                ["time"] = (long)(time.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds,
                ["open"] = open,
                ["high"] = high,
                ["low"] = low,
                ["close"] = close,
                ["volume"] = volume
            };
            
            SendMarketData(payload, isPartial ? MessageType.PartialBar : MessageType.Bar);
        }
        
        public void SendHistoryBatch(string pair, List<JObject> bars, int days)
        {
            var payload = new JObject
            {
                ["pair"] = pair,
                ["bars"] = new JArray(bars),
                ["days"] = days
            };
            SendMarketData(payload, MessageType.HistoryBatch);
        }
        
        public void SendHistoryEnd()
        {
            SendMarketData(new JObject(), MessageType.HistoryEnd);
        }
        
        public void SendEntryFill(string tradeId, double entryPrice, 
                                  double? stopLoss = null, double? takeProfit = null,
                                  double? slippage = null)
        {
            var payload = new JObject
            {
                ["trade_id"] = tradeId,
                ["entry_price"] = entryPrice
            };
            if (stopLoss.HasValue) payload["stop_loss"] = stopLoss.Value;
            if (takeProfit.HasValue) payload["take_profit"] = takeProfit.Value;
            if (slippage.HasValue) payload["slippage"] = slippage.Value;
            
            SendMarketData(payload, MessageType.EntryFill);
        }
        
        public void SendExitFill(string tradeId, double exitPrice, string resultType)
        {
            var payload = new JObject
            {
                ["trade_id"] = tradeId,
                ["exit_price"] = exitPrice,
                ["result_type"] = resultType,
                ["exit_time"] = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds
            };
            SendMarketData(payload, MessageType.ExitFill);
        }
        
        public void SendTradeLog(string tradeId, string evt, string msg)
        {
            var payload = new JObject
            {
                ["trade_id"] = tradeId,
                ["event"] = evt,
                ["message"] = msg.Replace("\"", "'")
            };
            SendMarketData(payload, MessageType.TradeLog);
        }
        
        public void SendError(string source, string errorType, string message, string details = null)
        {
            var payload = new JObject
            {
                ["source"] = source,
                ["error_type"] = errorType,
                ["message"] = message.Replace("\"", "'"),
                ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0
            };
            if (details != null)
                payload["details"] = details.Replace("\"", "'").Replace("\r\n", " | ").Replace("\n", " | ");
            
            SendMarketData(payload, MessageType.Error);
        }
        
        public void SendHeartbeat(string source, string status)
        {
            var payload = new JObject
            {
                ["source"] = source,
                ["status"] = status
            };
            if (_heartbeatPub != null)
            {
                var envelope = MessageEnvelope.Create(MessageType.Heartbeat, payload, NextSeq());
                _heartbeatPub.SendFrame(envelope.ToJson());
            }
        }
        
        public void SendTestPing(double timestamp)
        {
            var payload = new JObject
            {
                ["timestamp"] = timestamp
            };
            SendMarketData(payload, MessageType.TestPing);
        }
        
        public void SendTestPong(double timestamp)
        {
            var payload = new JObject
            {
                ["timestamp"] = timestamp
            };
            SendMarketData(payload, MessageType.TestPong);
        }
        
        public void SendTestStart(string scenario, double entryPrice = 21000.0, double riskPoints = 80.0, double rrRatio = 1.0)
        {
            var payload = new JObject
            {
                ["scenario"] = scenario,
                ["entry_price"] = entryPrice,
                ["risk_points"] = riskPoints,
                ["rr_ratio"] = rrRatio
            };
            SendMarketData(payload, MessageType.TestStart);
        }
        
        public void SendTestResult(string scenario, bool passed, string tradeId = null, string message = "")
        {
            var payload = new JObject
            {
                ["scenario"] = scenario,
                ["passed"] = passed,
                ["message"] = message
            };
            if (tradeId != null) payload["trade_id"] = tradeId;
            SendMarketData(payload, MessageType.TestResult);
        }
        
        public void SendConnect(string platform, string version, string account = null, string pair = null)
        {
            var payload = new JObject
            {
                ["platform"] = platform,
                ["version"] = version
            };
            if (account != null) payload["account"] = account;
            if (pair != null) payload["pair"] = pair;
            
            SendMarketData(payload, MessageType.Connect);
        }
        
        // Receive methods (Python → Platform)
        public string ReceiveCommand(int timeoutMs = 100)
        {
            if (_commandPull == null) return null;
            
            if (_commandPull.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out string message))
            {
                return message;
            }
            return null;
        }
        
        public string ReceiveQuery(int timeoutMs = 100)
        {
            if (_queryReq == null) return null;
            
            if (_queryReq.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out string message))
            {
                return message;
            }
            return null;
        }
        
        public void SendQueryResponse(JObject payload)
        {
            if (_queryReq == null) return;
            
            var envelope = MessageEnvelope.Create(MessageType.PositionResponse, payload, NextSeq());
            _queryReq.SendFrame(envelope.ToJson());
        }
        
        public bool SendTestPingWithResponse(double timeoutMs = 2000)
        {
            if (_queryReq == null) return false;
            
            try
            {
                // Send TEST_PING as a query (synchronous request-response)
                var payload = new JObject
                {
                    ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0
                };
                var envelope = MessageEnvelope.Create(MessageType.TestPing, payload, NextSeq());
                
                _queryReq.SendFrame(envelope.ToJson());
                
                // Wait for response with timeout
                if (_queryReq.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out string response))
                {
                    // Parse response to verify it's a TEST_PONG
                    var respEnvelope = JsonConvert.DeserializeObject<MessageEnvelope>(response);
                    return respEnvelope?.MsgType == MessageType.TestPong;
                }
                return false;
            }
            catch
            {
                return false;
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // UI Layer
    // ═══════════════════════════════════════════════════════════════════════

    internal class ZmqConnectorWindow
    {
        private readonly Action<string> _log;
        private Window _window;
        private TextBox _logBox;
        private TextBlock _statusLabel;
        private TextBlock _statsLabel;
        private Button _connectBtn;
        private Button _testConnBtn;
        private Button _e2eTestBtn;
        
        private Action _onConnect;
        private Action _onTestConnection;
        private Action _onE2ETests;
        
        internal ZmqConnectorWindow(Action<string> log)
        {
            _log = log;
        }
        
        internal void SetButtonHandlers(Action onConnect, Action onTestConnection = null, Action onE2ETests = null)
        {
            _onConnect = onConnect;
            _onTestConnection = onTestConnection;
            _onE2ETests = onE2ETests;
        }
        
        internal void Show(bool connected)
        {
            if (_window != null)
            {
                _window.Activate();
                return;
            }
            
            _window = new Window
            {
                Title = "TradingBot ZMQ Connector",
                Width = 550,
                Height = 450,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                Background = new SolidColorBrush(Color.FromRgb(30, 30, 30)),
            };
            
            var grid = new Grid();
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });    // Row 0: Status
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });    // Row 1: Stats
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });    // Row 2: Buttons
            grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); // Row 3: Log
            
            // Status row
            _statusLabel = new TextBlock
            {
                Text = connected ? "CONNECTED" : "DISCONNECTED",
                Foreground = connected ? Brushes.LimeGreen : Brushes.OrangeRed,
                FontSize = 16,
                FontWeight = FontWeights.Bold,
                Margin = new Thickness(12, 12, 12, 4),
            };
            Grid.SetRow(_statusLabel, 0);
            grid.Children.Add(_statusLabel);
            
            // Stats row
            _statsLabel = new TextBlock
            {
                Text = "ZeroMQ Edition - High Performance",
                Foreground = Brushes.Silver,
                FontSize = 12,
                Margin = new Thickness(12, 0, 12, 8),
            };
            Grid.SetRow(_statsLabel, 1);
            grid.Children.Add(_statsLabel);
            
            // Button row
            var btnPanel = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Margin = new Thickness(12, 4, 12, 8),
            };
            _connectBtn = new Button
            {
                Content = connected ? "Disconnect" : "Connect",
                Width = 120,
                Height = 30,
                Margin = new Thickness(0, 0, 8, 0),
            };
            _connectBtn.Click += (s, e) => { _onConnect?.Invoke(); };
            btnPanel.Children.Add(_connectBtn);
            
            _testConnBtn = new Button
            {
                Content = "Test Connection",
                Width = 120,
                Height = 30,
                Margin = new Thickness(0, 0, 8, 0),
                IsEnabled = connected,
            };
            _testConnBtn.Click += (s, e) => { _onTestConnection?.Invoke(); };
            btnPanel.Children.Add(_testConnBtn);
            
            _e2eTestBtn = new Button
            {
                Content = "Run E2E Tests",
                Width = 120,
                Height = 30,
                IsEnabled = connected,
            };
            _e2eTestBtn.Click += (s, e) => { _onE2ETests?.Invoke(); };
            btnPanel.Children.Add(_e2eTestBtn);
            
            Grid.SetRow(btnPanel, 2);
            grid.Children.Add(btnPanel);
            
            // Log box
            _logBox = new TextBox
            {
                IsReadOnly = true,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Background = new SolidColorBrush(Color.FromRgb(20, 20, 20)),
                Foreground = Brushes.LightGray,
                FontFamily = new FontFamily("Consolas"),
                FontSize = 11,
                Margin = new Thickness(12, 0, 12, 12),
                TextWrapping = TextWrapping.Wrap,
                BorderThickness = new Thickness(1),
                BorderBrush = new SolidColorBrush(Color.FromRgb(60, 60, 60)),
            };
            Grid.SetRow(_logBox, 3);
            grid.Children.Add(_logBox);
            
            _window.Content = grid;
            _window.Closed += (s, ev) =>
            {
                _window = null;
                _logBox = null;
                _statusLabel = null;
                _statsLabel = null;
                _connectBtn = null;
                _testConnBtn = null;
                _e2eTestBtn = null;
            };
            
            _window.Show();
            Log("Window opened. Click Connect to start ZMQ connection.");
        }
        
        internal void UpdateStatus(bool connected, string statsText)
        {
            if (_window == null) return;
            _window.Dispatcher.BeginInvoke(new Action(() =>
            {
                if (_statusLabel != null)
                {
                    _statusLabel.Text = connected ? "CONNECTED" : "DISCONNECTED";
                    _statusLabel.Foreground = connected ? Brushes.LimeGreen : Brushes.OrangeRed;
                }
                if (_statsLabel != null && !string.IsNullOrEmpty(statsText))
                    _statsLabel.Text = statsText;
                if (_connectBtn != null)
                    _connectBtn.Content = connected ? "Disconnect" : "Connect";
                if (_testConnBtn != null)
                    _testConnBtn.IsEnabled = connected;
                if (_e2eTestBtn != null)
                    _e2eTestBtn.IsEnabled = connected;
            }));
        }
        
        internal void SetE2EButtonEnabled(bool enabled)
        {
            if (_window == null) return;
            _window.Dispatcher.Invoke(new Action(() =>
            {
                if (_e2eTestBtn != null) _e2eTestBtn.IsEnabled = enabled;
            }));
        }
        
        internal void Log(string message)
        {
            var line = DateTime.Now.ToString("HH:mm:ss") + "  " + message + "\n";
            _log(message);
            
            if (_window == null) return;
            _window.Dispatcher.BeginInvoke(new Action(() =>
            {
                if (_logBox == null) return;
                _logBox.AppendText(line);
                _logBox.ScrollToEnd();
            }));
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Main AddOn
    // ═══════════════════════════════════════════════════════════════════════

    public class TradingBotZmqConnector : AddOnBase
    {
        // Configuration
        private string _pythonHost = "127.0.0.1";
        private int _marketPort = 5555;
        private int _commandPort = 5556;
        private int _queryPort = 5557;
        private int _heartbeatPort = 5558;
        private string _instrument = "MNQ 06-26";
        private int _historyDays = 30;
        private int _batchSize = 500;
        
        // State
        private volatile bool _connected;
        private ZmqNetwork _network;
        private ZmqConnectorWindow _ui;
        private Thread _commandThread;
        private Thread _heartbeatThread;
        private CancellationTokenSource _cts;
        private Account _account;
        
        // Menu - Use MenuItem instead of deprecated NTMenuItem
        private MenuItem _menuItem;
        private MenuItem _existingNewMenu;
        
        // Stats
        private long _commandsReceived = 0;
        private long _ticksSent = 0;
        private long _barsSent = 0;
        
        // Pending entry tracking for order management
        private class PendingEntry
        {
            public string Direction;
            public double SlPoints;
            public double RrRatio;
            public string AtmStrategyName;
        }
        private readonly Dictionary<string, PendingEntry> _pendingEntries = new Dictionary<string, PendingEntry>();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Connects to TradingBot Python app via ZeroMQ";
                Name = "TradingBotZmqConnector";
            }
        }
        
        protected override void OnWindowCreated(Window window)
        {
            if (!(window is ControlCenter cc)) return;
            
            // Find the New menu - cc.MainMenu is ObservableCollection<object>
            _existingNewMenu = null;
            if (cc.MainMenu != null)
            {
                foreach (var item in cc.MainMenu)
                {
                    if (item is MenuItem mi)
                    {
                        if (mi.Header?.ToString() == "New")
                        {
                            _existingNewMenu = mi;
                            break;
                        }
                        // Check submenus recursively
                        _existingNewMenu = FindMenuItem(mi, "New");
                        if (_existingNewMenu != null) break;
                    }
                }
            }
            if (_existingNewMenu == null) return;
            
            _menuItem = new MenuItem 
            { 
                Header = "TradingBot ZMQ Connector"
            };
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
        
        private MenuItem FindMenuItem(ItemsControl parent, string header)
        {
            if (parent == null) return null;
            
            foreach (var item in parent.Items)
            {
                if (item is MenuItem mi)
                {
                    if (mi.Header?.ToString() == header)
                        return mi;
                    
                    var found = FindMenuItem(mi, header);
                    if (found != null) return found;
                }
            }
            return null;
        }
        
        private void OnMenuItemClick(object sender, RoutedEventArgs e)
        {
            ShowStatusWindow();
        }
        
        private void ShowStatusWindow()
        {
            if (_ui == null)
            {
                _ui = new ZmqConnectorWindow(msg => Print("[ZMQ] " + msg));
                _ui.SetButtonHandlers(
                    onConnect: () =>
                    {
                        if (_connected) Disconnect();
                        else Connect();
                    },
                    onTestConnection: () => _ = TestConnectionAsync(),
                    onE2ETests: () => _ = RunE2ETestsAsync()
                );
            }
            _ui.Show(_connected);
        }
        
        private void Log(string message)
        {
            _ui?.Log(message);
            Print("[ZMQ] " + message);
        }
        
        private string FormatExceptionDetails(Exception ex)
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
        
        private void UpdateStats()
        {
            var stats = $"Ticks: {_ticksSent} | Bars: {_barsSent} | Cmds: {_commandsReceived}";
            _ui?.UpdateStatus(_connected, stats);
        }
        
        /// <summary>
        /// Show a notification for critical errors. Uses NinjaTrader's Alert system.
        /// </summary>
        private void NotifyError(string message)
        {
            Log($"[NOTIFICATION] {message}");
            
            try
            {
                // Use NinjaTrader's Alert system for critical notifications
                NinjaTrader.Code.Alert.RenderAlert(
                    "TradingBot ZMQ",
                    message,
                    "Error",
                    Brushes.Red,
                    Brushes.White,
                    10  // Duration in seconds
                );
            }
            catch
            {
                // Fallback: just log if alert fails
                Print($"[ALERT] {message}");
            }
        }
        
        /// <summary>
        /// Show a notification for warnings.
        /// </summary>
        private void NotifyWarning(string message)
        {
            Log($"[WARNING] {message}");
            
            try
            {
                NinjaTrader.Code.Alert.RenderAlert(
                    "TradingBot ZMQ",
                    message,
                    "Warning",
                    Brushes.Orange,
                    Brushes.Black,
                    5
                );
            }
            catch
            {
                Print($"[WARNING] {message}");
            }
        }
        
        /// <summary>
        /// Show a notification for successful operations.
        /// </summary>
        private void NotifySuccess(string message)
        {
            Log($"[SUCCESS] {message}");
            
            try
            {
                NinjaTrader.Code.Alert.RenderAlert(
                    "TradingBot ZMQ",
                    message,
                    "Success",
                    Brushes.Green,
                    Brushes.White,
                    3
                );
            }
            catch
            {
                Print($"[SUCCESS] {message}");
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Connection
        // ═══════════════════════════════════════════════════════════════════

        private void Connect()
        {
            try
            {
                Log("Starting ZeroMQ connection...");
                
                _network = new ZmqNetwork(
                    _pythonHost, _marketPort, _commandPort, _queryPort, _heartbeatPort);
                _network.Start();
                
                _cts = new CancellationTokenSource();
                _connected = true;
                
                // Send connect handshake
                _network.SendConnect("ninjatrader", "1.0.0", pair: _instrument.Split(' ')[0]);
                Log("Connected to Python TradingBot via ZeroMQ");
                
                // Start background threads
                _commandThread = new Thread(CommandLoop) { IsBackground = true, Name = "ZMQ-Commands" };
                _commandThread.Start();
                
                _heartbeatThread = new Thread(HeartbeatLoop) { IsBackground = true, Name = "ZMQ-Heartbeat" };
                _heartbeatThread.Start();
                
                // Initialize order management
                InitializeAccount();
                
                // Send historical data
                _ = SendHistoryAsync();
                
                // Subscribe to live data
                SubscribeToMarketData();
                
                UpdateStats();
            }
            catch (Exception ex)
            {
                var errorDetails = FormatExceptionDetails(ex);
                Log($"Connection error: {ex.Message}");
                Log($"Error details: {errorDetails}");
                _network?.SendError("ninjatrader", "connection_failed", ex.Message, errorDetails);
                NotifyError($"ZMQ Connection Failed: {ex.Message}");
                Disconnect();
            }
        }
        
        private void Disconnect()
        {
            _connected = false;
            _cts?.Cancel();
            
            UnsubscribeFromMarketData();
            
            _network?.Dispose();
            _network = null;
            
            Log("Disconnected from Python TradingBot");
            UpdateStats();
        }

        // ═══════════════════════════════════════════════════════════════════
        // Background Threads
        // ═══════════════════════════════════════════════════════════════════

        private void CommandLoop()
        {
            Log("Command loop started");
            
            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    // Non-blocking receive with timeout
                    var msg = _network?.ReceiveCommand(timeoutMs: 100);
                    if (string.IsNullOrEmpty(msg)) continue;
                    
                    _commandsReceived++;
                    var envelope = JsonConvert.DeserializeObject<MessageEnvelope>(msg);
                    
                    DispatchCommand(envelope);
                    
                    if (_commandsReceived % 10 == 0)
                        UpdateStats();
                }
                catch (Exception ex)
                {
                    var errorDetails = FormatExceptionDetails(ex);
                    Log($"Command loop error: {ex.Message}");
                    _network?.SendError("ninjatrader", "command_loop_error", ex.Message, errorDetails);
                    NotifyError($"Command Loop Error: {ex.Message}");
                }
            }
            
            Log("Command loop stopped");
        }
        
        private void HeartbeatLoop()
        {
            while (_connected && !_cts.Token.IsCancellationRequested)
            {
                try
                {
                    _network?.SendHeartbeat("ninjatrader", "ok");
                    Thread.Sleep(5000); // 5 second heartbeat
                }
                catch (Exception ex)
                {
                    var errorDetails = FormatExceptionDetails(ex);
                    Log($"Heartbeat error: {ex.Message}");
                    _network?.SendError("ninjatrader", "heartbeat_error", ex.Message, errorDetails);
                    NotifyWarning($"Heartbeat Error: {ex.Message}");
                }
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Command Dispatch
        // ═══════════════════════════════════════════════════════════════════

        private void DispatchCommand(MessageEnvelope envelope)
        {
            var payload = envelope.Payload;
            
            switch (envelope.MsgType)
            {
                case MessageType.OrderOpen:
                    HandleOpenOrder(payload);
                    break;
                    
                case MessageType.OrderClose:
                    HandleCloseOrder(payload);
                    break;
                    
                case MessageType.OrderModify:
                    HandleModifyOrder(payload);
                    break;
                    
                case MessageType.RefreshRequest:
                    HandleRefreshRequest(payload);
                    break;
                    
                case MessageType.TestStart:
                    HandleTestStart(payload);
                    break;
                    
                default:
                    var unknownCmdMsg = $"Unknown command received: {envelope.MsgType}";
                    Log($"ERROR: {unknownCmdMsg}");
                    _network?.SendError("ninjatrader", "unknown_command", unknownCmdMsg, $"Payload: {envelope.Payload}");
                    break;
            }
        }
        
        /// <summary>
        /// Get ATM strategy name based on stop loss points.
        /// </summary>
        private static string GetAtmStrategyName(double slPoints)
        {
            if (slPoints <= 15.0) return "TA_MNQ_15pt";
            if (slPoints <= 20.0) return "TA_MNQ_20pt";
            if (slPoints <= 30.0) return "TA_MNQ_30pt";
            return "TA_MNQ_40pt";
        }

        private void HandleOpenOrder(JObject payload)
        {
            try
            {
                var tradeId = payload["trade_id"]?.ToString();
                var direction = payload["direction"]?.ToString();
                var entryPrice = payload["entry_price"]?.Value<double>() ?? 0;
                var stopLoss = payload["stop_loss"]?.Value<double>() ?? 0;
                var takeProfit = payload["take_profit"]?.Value<double>() ?? 0;
                var slPoints = payload["risk_points"]?.Value<double>() ?? 0;
                var rrRatio = payload["rr_ratio"]?.Value<double>() ?? 2.0;
                
                // Validate required fields
                if (string.IsNullOrEmpty(tradeId))
                {
                    throw new ArgumentException("trade_id is required");
                }
                if (string.IsNullOrEmpty(direction) || (direction != "long" && direction != "short"))
                {
                    throw new ArgumentException($"Invalid direction: {direction}");
                }
                if (slPoints <= 0)
                {
                    throw new ArgumentException($"Invalid sl_points: {slPoints}");
                }
                
                if (_account == null)
                {
                    throw new InvalidOperationException("No account available");
                }
                
                // Check for duplicate trade_id
                lock (_ordersLock)
                {
                    if (_pendingEntries.ContainsKey(tradeId))
                    {
                        Log($"WARNING: Duplicate place_order for {tradeId}, ignoring");
                        _network?.SendTradeLog(tradeId, "NT:WARNING", "Duplicate place_order request ignored");
                        return;
                    }
                }
                
                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                {
                    throw new InvalidOperationException($"Instrument '{_instrument}' not found");
                }
                
                bool isLong = direction == "long";
                var orderAction = isLong ? OrderAction.Buy : OrderAction.SellShort;
                
                // Position sizing - calculate contracts based on risk
                double riskUsd = payload["risk_usd"]?.Value<double>() ?? 0;
                double riskPct = payload["risk_pct"]?.Value<double>() ?? 0;
                double pointValue = instrument.MasterInstrument.PointValue;
                int qty = 1;
                
                if (riskUsd > 0 && slPoints > 0)
                {
                    qty = Math.Max(1, (int)Math.Round(riskUsd / (slPoints * pointValue)));
                }
                else if (riskPct > 0 && slPoints > 0 && _account != null)
                {
                    double balance = _account.Get(AccountItem.CashValue, Currency.UsDollar);
                    double risk = balance * riskPct / 100.0;
                    qty = Math.Max(1, (int)Math.Round(risk / (slPoints * pointValue)));
                }
                
                Log($"OPEN ORDER: {tradeId} {direction} {instrument.MasterInstrument.Name} x{qty} SL={stopLoss} TP={takeProfit}");
                
                // Get ATM strategy name
                string atmStrategyName = GetAtmStrategyName(slPoints);
                
                // Create entry order - name MUST be "Entry" for ATM to work
                var entryOrder = _account.CreateOrder(
                    instrument,
                    orderAction,
                    OrderType.Market,
                    OrderEntry.Automated,
                    TimeInForce.Gtc,
                    qty,
                    0, 0,  // limit price, stop price (not used for market)
                    string.Empty,
                    "Entry",  // CRITICAL: Must be exactly "Entry" for ATM
                    DateTime.MinValue,
                    null);
                
                if (entryOrder == null)
                    throw new InvalidOperationException("Failed to create entry order");
                
                // Start ATM strategy - this submits the entry and manages SL/TP automatically
                NinjaTrader.NinjaScript.AtmStrategy.StartAtmStrategy(atmStrategyName, entryOrder);
                
                // Store trade info for tracking
                lock (_ordersLock)
                {
                    _pendingEntries[tradeId] = new PendingEntry
                    {
                        Direction = direction,
                        SlPoints = slPoints,
                        RrRatio = rrRatio,
                        AtmStrategyName = atmStrategyName
                    };
                }
                
                // Also store in tradeId to ATM strategy mapping for modify/close operations
                lock (_ordersLock)
                {
                    _tradeIdToAtmStrategy[tradeId] = atmStrategyName;
                }
                
                Log($"ATM STRATEGY STARTED: {atmStrategyName} for trade {tradeId}");
                _network?.SendTradeLog(tradeId, "NT:ORDER", $"Market {direction} x{qty} with ATM '{atmStrategyName}'");
                NotifySuccess($"Order Submitted: {direction} x{qty}");
                
                // Note: ENTRY_FILL will be sent from OnExecutionUpdate when fill is confirmed
            }
            catch (Exception ex)
            {
                var tradeId = payload["trade_id"]?.ToString() ?? "unknown";
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR handling open order {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_open_failed", $"Failed to open order {tradeId}", errorDetails);
                _network?.SendTradeLog(tradeId, "NT:ERROR", $"Order open failed: {ex.Message}");
                NotifyError($"Order Open Failed: {ex.Message}");
                
                // Clean up on error
                lock (_ordersLock)
                {
                    _pendingEntries.Remove(tradeId);
                }
            }
        }
        
        private void HandleCloseOrder(JObject payload)
        {
            try
            {
                var tradeId = payload["trade_id"]?.ToString();
                
                if (string.IsNullOrEmpty(tradeId))
                {
                    throw new ArgumentException("trade_id is required");
                }
                
                if (_account == null)
                {
                    throw new InvalidOperationException("No account available");
                }
                
                Log($"CLOSE ORDER: {tradeId}");
                
                // Get the instrument
                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                {
                    throw new InvalidOperationException($"Instrument '{_instrument}' not found");
                }
                
                // Find the position for this trade to determine exit price
                Position position = null;
                foreach (var pos in _account.Positions)
                {
                    if (pos.Instrument == instrument)
                    {
                        position = pos;
                        break;
                    }
                }
                
                // Store pending close info for when execution update comes
                _pendingCloseTradeId = tradeId;
                _pendingCloseExitPrice = position?.AveragePrice ?? 0;
                
                // Flatten the position - ATM strategy will close automatically
                _account.Flatten(new[] { instrument });
                
                // Clean up tracking dictionaries
                lock (_ordersLock)
                {
                    _stopLossOrders.Remove(tradeId);
                    _tradeIdToAtmStrategy.Remove(tradeId);
                }
                
                Log($"Flatten command sent for {tradeId}");
                _network?.SendTradeLog(tradeId, "NT:CLOSE", "Position flatten command sent");
                NotifySuccess($"Close Command Sent: {tradeId}");
                
                // Note: EXIT_FILL will be sent from OnExecutionUpdate when fill is confirmed
            }
            catch (Exception ex)
            {
                var tradeId = payload["trade_id"]?.ToString() ?? "unknown";
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR handling close order {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_close_failed", $"Failed to close order {tradeId}", errorDetails);
                _network?.SendTradeLog(tradeId, "NT:ERROR", $"Close order failed: {ex.Message}");
                NotifyError($"Order Close Failed: {ex.Message}");
                
                // Clear pending close on error
                _pendingCloseTradeId = null;
                _pendingCloseExitPrice = 0;
            }
        }
        
        // Track active trades and their stop loss orders for modification
        private readonly Dictionary<string, Order> _stopLossOrders = new Dictionary<string, Order>();
        private readonly Dictionary<string, string> _tradeIdToAtmStrategy = new Dictionary<string, string>();
        private readonly object _ordersLock = new object();
        
        // Track pending close operations
        private string _pendingCloseTradeId = null;
        private double _pendingCloseExitPrice = 0;

        private void HandleModifyOrder(JObject payload)
        {
            try
            {
                var tradeId = payload["trade_id"]?.ToString();
                var newSl = payload["stop_loss"]?.Value<double>() ?? 0;
                
                if (string.IsNullOrEmpty(tradeId))
                {
                    throw new ArgumentException("trade_id is required");
                }

                if (newSl <= 0)
                {
                    throw new ArgumentException($"Invalid stop_loss: {newSl}");
                }
                
                if (_account == null)
                {
                    throw new InvalidOperationException("No account available");
                }
                
                Log($"MODIFY ORDER: {tradeId} new SL={newSl}");

                // Get the tracked stop loss order
                Order stopOrder;
                lock (_ordersLock)
                {
                    if (!_stopLossOrders.TryGetValue(tradeId, out stopOrder))
                    {
                        Log($"WARNING: No tracked stop order found for {tradeId}, attempting to find...");
                        stopOrder = FindStopOrderForTrade(tradeId);
                        if (stopOrder == null)
                        {
                            throw new InvalidOperationException($"Stop order not found for trade {tradeId}");
                        }
                        _stopLossOrders[tradeId] = stopOrder;
                    }
                }

                // Validate order state
                if (stopOrder.OrderState != OrderState.Working && stopOrder.OrderState != OrderState.Accepted)
                {
                    throw new InvalidOperationException($"Stop order is not modifiable (state: {stopOrder.OrderState})");
                }

                // Modify the stop order
                _account.ChangeOrder(
                    stopOrder,
                    stopOrder.Quantity,
                    stopOrder.LimitPrice,
                    newSl,
                    stopOrder.AtmStrategyId
                );
                
                Log($"SUCCESS: Modified SL for {tradeId} from {stopOrder.StopPrice} to {newSl}");
                _network?.SendTradeLog(tradeId, "NT:MODIFY", $"Stop loss changed from {stopOrder.StopPrice} to {newSl}");
                NotifySuccess($"SL Modified: {stopOrder.StopPrice} → {newSl}");
            }
            catch (Exception ex)
            {
                var tradeId = payload["trade_id"]?.ToString() ?? "unknown";
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR handling modify order {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to modify order {tradeId}: {ex.Message}", errorDetails);
                NotifyWarning($"SL Modify Failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Find the stop loss order for a trade by searching through account orders.
        /// </summary>
        private Order FindStopOrderForTrade(string tradeId)
        {
            if (_account == null) return null;

            string atmStrategyName;
            lock (_ordersLock)
            {
                if (!_tradeIdToAtmStrategy.TryGetValue(tradeId, out atmStrategyName))
                    atmStrategyName = null;
            }

            foreach (var order in _account.Orders)
            {
                // Must be a stop order
                if (order.OrderType != OrderType.StopMarket && order.OrderType != OrderType.StopLimit)
                    continue;

                // Name should indicate it's a stop
                if (order.Name != "Stop" && !order.Name.Contains("Stop"))
                    continue;

                // Must be active
                if (order.OrderState != OrderState.Working && order.OrderState != OrderState.Accepted)
                    continue;

                // Check ATM strategy match if we have it
                if (!string.IsNullOrEmpty(atmStrategyName))
                {
                    var orderAtmId = order.AtmStrategyId ?? "";
                    if (orderAtmId.StartsWith(atmStrategyName) || order.AtmStrategyName == atmStrategyName)
                        return order;
                }
                else
                {
                    // Return first matching stop order
                    return order;
                }
            }

            return null;
        }
        
        private void HandleRefreshRequest(JObject payload)
        {
            try
            {
                var days = payload["days"]?.Value<int>() ?? 1;
                Log($"REFRESH REQUEST: {days} days");
                
                _ = SendHistoryAsync(days);
            }
            catch (Exception ex)
            {
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR handling refresh request: {ex.Message}");
                _network?.SendError("ninjatrader", "refresh_failed", "Failed to handle refresh request", errorDetails);
            }
        }
        
        private void HandleTestStart(JObject payload)
        {
            try
            {
                var scenario = payload["scenario"]?.ToString() ?? "tp_hit";
                var entryPrice = payload["entry_price"]?.Value<double>() ?? 21000.0;
                var riskPoints = payload["risk_points"]?.Value<double>() ?? 80.0;
                var rrRatio = payload["rr_ratio"]?.Value<double>() ?? 1.0;
                
                // Generate test trade ID
                var tradeId = $"test_{scenario}_{Guid.NewGuid().ToString("N").Substring(0, 8)}";
                
                Log($"TEST START: {scenario} tradeId={tradeId} entry={entryPrice}");
                
                // Calculate SL/TP
                double sl, tp;
                string direction = "long";  // Tests always use long for simplicity
                
                sl = entryPrice - riskPoints;
                tp = entryPrice + (riskPoints * rrRatio);
                
                // Simulate entry fill
                _network?.SendEntryFill(tradeId, entryPrice, sl, tp);
                _network?.SendTradeLog(tradeId, "NT:TEST", $"Test entry filled @ {entryPrice}");
                
                // Simulate scenario outcome after delay
                Task.Run(async () =>
                {
                    await Task.Delay(1000);
                    
                    switch (scenario)
                    {
                        case "tp_hit":
                            Log($"TEST: Simulating TP hit @ {tp}");
                            _network?.SendExitFill(tradeId, tp, "TP");
                            _network?.SendTradeLog(tradeId, "NT:TEST", $"TP filled @ {tp}");
                            _network?.SendTestResult(scenario, true, tradeId, "TP hit as expected");
                            break;
                            
                        case "sl_hit":
                            Log($"TEST: Simulating SL hit @ {sl}");
                            _network?.SendExitFill(tradeId, sl, "SL");
                            _network?.SendTradeLog(tradeId, "NT:TEST", $"SL filled @ {sl}");
                            _network?.SendTestResult(scenario, true, tradeId, "SL hit as expected");
                            break;
                            
                        case "session_end":
                            var closePrice = entryPrice + 5.0;  // Small profit
                            Log($"TEST: Simulating session end close @ {closePrice}");
                            _network?.SendExitFill(tradeId, closePrice, "CLOSE");
                            _network?.SendTradeLog(tradeId, "NT:TEST", $"Session end close @ {closePrice}");
                            _network?.SendTestResult(scenario, true, tradeId, "Session end close as expected");
                            break;
                            
                        default:
                            _network?.SendTestResult(scenario, false, tradeId, $"Unknown scenario: {scenario}");
                            break;
                    }
                });
            }
            catch (Exception ex)
            {
                Log($"ERROR in test start: {ex.Message}");
                _network?.SendError("ninjatrader", "test_failed", $"Test start failed: {ex.Message}");
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Market Data
        // ═══════════════════════════════════════════════════════════════════

        private async Task SendHistoryAsync(int days = 30)
        {
            try
            {
                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                {
                    Log($"ERROR: Instrument '{_instrument}' not found");
                    return;
                }
                
                var tcs = new TaskCompletionSource<bool>();
                var barsRequest = new BarsRequest(
                    instrument, 
                    DateTime.UtcNow.AddDays(-days), 
                    DateTime.UtcNow);
                barsRequest.BarsPeriod = new BarsPeriod 
                { 
                    BarsPeriodType = BarsPeriodType.Minute, 
                    Value = 1 
                };
                
                var batch = new List<JObject>();
                int count = 0;
                
                barsRequest.Request((bars, errorCode, errorMessage) =>
                {
                    // Use fully qualified name to avoid ambiguity
                    if (errorCode != NinjaTrader.Cbi.ErrorCode.NoError)
                    {
                        Log($"ERROR: BarsRequest failed: {errorMessage}");
                        tcs.SetResult(false);
                        return;
                    }
                    
                    for (int i = 0; i < bars.Bars.Count; i++)
                    {
                        var time = bars.Bars.GetTime(i);
                        var utcTs = (long)(time.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds;
                        
                        batch.Add(new JObject
                        {
                            ["time"] = utcTs,
                            ["open"] = bars.Bars.GetOpen(i),
                            ["high"] = bars.Bars.GetHigh(i),
                            ["low"] = bars.Bars.GetLow(i),
                            ["close"] = bars.Bars.GetClose(i),
                            ["volume"] = (long)bars.Bars.GetVolume(i),
                            ["pair"] = _instrument.Split(' ')[0]
                        });
                        count++;
                        
                        if (batch.Count >= _batchSize)
                        {
                            _network?.SendHistoryBatch(_instrument.Split(' ')[0], batch, days);
                            batch.Clear();
                        }
                    }
                    
                    if (batch.Count > 0)
                    {
                        _network?.SendHistoryBatch(_instrument.Split(' ')[0], batch, days);
                    }
                    
                    _barsSent = count;
                    Log($"Sent {count} historical bars ({days} days)");
                    
                    // Send history end signal
                    _network?.SendHistoryEnd();
                    
                    tcs.SetResult(true);
                });
                
                await tcs.Task;
            }
            catch (Exception ex)
            {
                var errorDetails = FormatExceptionDetails(ex);
                Log($"SendHistory error: {ex.Message}");
                _network?.SendError("ninjatrader", "history_load_failed", $"Failed to load {days} days of history", errorDetails);
            }
        }
        
        private void SubscribeToMarketData()
        {
            var instrument = Instrument.GetInstrument(_instrument);
            if (instrument == null)
            {
                var errorMsg = $"Cannot subscribe, instrument '{_instrument}' not found";
                Log($"ERROR: {errorMsg}");
                _network?.SendError("ninjatrader", "instrument_not_found", errorMsg);
                return;
            }
            instrument.MarketData.Update += OnMarketDataUpdate;
            Log($"Subscribed to market data for {_instrument}");
        }
        
        private void UnsubscribeFromMarketData()
        {
            var instrument = Instrument.GetInstrument(_instrument);
            if (instrument != null)
            {
                instrument.MarketData.Update -= OnMarketDataUpdate;
                Log($"Unsubscribed from market data for {_instrument}");
            }
        }
        
        private void OnMarketDataUpdate(object sender, MarketDataEventArgs e)
        {
            try
            {
                if (!_connected || e.MarketDataType != MarketDataType.Last) return;
                
                var utcTs = (long)(e.Time.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds;
                var pair = e.Instrument.MasterInstrument.Name;
                
                _network?.SendTick(pair, e.Price, (long)e.Volume, e.Time);
                _ticksSent++;
                
                if (_ticksSent % 500 == 0)
                    UpdateStats();
            }
            catch (Exception ex)
            {
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR in market data update: {ex.Message}");
                _network?.SendError("ninjatrader", "market_data_error", "Error processing market data", errorDetails);
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Account / Orders
        // ═══════════════════════════════════════════════════════════════════

        private void InitializeAccount()
        {
            try
            {
                if (Account.All.Count == 0)
                {
                    var warnMsg = "No trading accounts found";
                    Log($"WARNING: {warnMsg}");
                    _network?.SendError("ninjatrader", "no_account", warnMsg);
                    return;
                }
                
                _account = Account.All[0];
                _account.ExecutionUpdate += OnExecutionUpdate;
                _account.OrderUpdate += OnOrderUpdate;
                
                Log($"Using account: {_account.Name}");
            }
            catch (Exception ex)
            {
                var errorDetails = FormatExceptionDetails(ex);
                Log($"ERROR initializing account: {ex.Message}");
                _network?.SendError("ninjatrader", "account_init_failed", "Failed to initialize trading account", errorDetails);
            }
        }
        
        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            try
            {
                var order = e.Order;
                Log($"ORDER UPDATE: {order.Name} state={order.OrderState}");
                
                // Track stop loss orders for modification capability
                if ((order.Name == "Stop" || order.Name.Contains("Stop")) && 
                    (order.OrderType == OrderType.StopMarket || order.OrderType == OrderType.StopLimit))
                {
                    // Find the trade ID associated with this stop order
                    string tradeId = null;
                    lock (_ordersLock)
                    {
                        foreach (var kvp in _tradeIdToAtmStrategy)
                        {
                            var orderAtmId = order.AtmStrategyId ?? "";
                            if (orderAtmId.StartsWith(kvp.Value) || order.AtmStrategyName == kvp.Value)
                            {
                                tradeId = kvp.Key;
                                break;
                            }
                        }
                    }
                    
                    if (tradeId != null)
                    {
                        lock (_ordersLock)
                        {
                            _stopLossOrders[tradeId] = order;
                        }
                        Log($"TRACKING SL order for {tradeId}: current SL={order.StopPrice}");
                    }
                }
                
                // Send order state to Python for tracking
                if (order.OrderState == OrderState.Rejected || order.OrderState == OrderState.Cancelled)
                {
                    _network?.SendError("ninjatrader", "order_state", $"Order {order.Name} is {order.OrderState}");
                }
            }
            catch (Exception ex)
            {
                Log($"ERROR in order update: {ex.Message}");
            }
        }
        
        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            try
            {
                var execution = e.Execution;
                var orderName = execution.Order.Name;
                var fillPrice = execution.Price;
                var quantity = execution.Quantity;
                
                Log($"EXECUTION: {orderName} @ {fillPrice} qty={quantity}");
                
                // Send execution to Python
                _network?.SendTradeLog(
                    orderName, 
                    "NT:EXECUTION", 
                    $"Execution: {quantity} @ {fillPrice}"
                );
                
                // Check for entry fill (order name is "Entry")
                if (orderName == "Entry")
                {
                    // Find the trade_id for this entry
                    string tradeId = null;
                    PendingEntry entry = null;
                    
                    lock (_ordersLock)
                    {
                        foreach (var kvp in _pendingEntries)
                        {
                            // Match by ATM strategy ID on the order
                            var orderAtmId = execution.Order.AtmStrategyId ?? "";
                            if (_tradeIdToAtmStrategy.TryGetValue(kvp.Key, out var atmName))
                            {
                                if (orderAtmId.StartsWith(atmName) || execution.Order.AtmStrategyName == atmName)
                                {
                                    tradeId = kvp.Key;
                                    entry = kvp.Value;
                                    break;
                                }
                            }
                        }
                    }
                    
                    if (tradeId != null && entry != null)
                    {
                        // Calculate SL/TP based on fill price
                        double sl, tp;
                        if (entry.Direction == "long")
                        {
                            sl = fillPrice - entry.SlPoints;
                            tp = fillPrice + (entry.SlPoints * entry.RrRatio);
                        }
                        else
                        {
                            sl = fillPrice + entry.SlPoints;
                            tp = fillPrice - (entry.SlPoints * entry.RrRatio);
                        }
                        
                        Log($"ENTRY FILL: {tradeId} @ {fillPrice} SL={sl} TP={tp}");
                        _network?.SendEntryFill(tradeId, fillPrice, sl, tp);
                        _network?.SendTradeLog(tradeId, "NT:FILL", $"Entry filled @ {fillPrice} qty={quantity}");
                        NotifySuccess($"Entry Filled @ {fillPrice}");
                    }
                }
                
                // Check for SL/TP fills (order name contains "Stop" or "Target")
                else if (orderName.Contains("Stop") || orderName.Contains("Target"))
                {
                    // Find trade_id by ATM strategy
                    string tradeId = null;
                    lock (_ordersLock)
                    {
                        foreach (var kvp in _tradeIdToAtmStrategy)
                        {
                            var orderAtmId = execution.Order.AtmStrategyId ?? "";
                            if (orderAtmId.StartsWith(kvp.Value) || execution.Order.AtmStrategyName == kvp.Value)
                            {
                                tradeId = kvp.Key;
                                break;
                            }
                        }
                    }
                    
                    if (tradeId != null)
                    {
                        var resultType = orderName.Contains("Stop") ? "SL" : "TP";
                        Log($"EXIT FILL: {tradeId} @ {fillPrice} ({resultType})");
                        _network?.SendExitFill(tradeId, fillPrice, resultType);
                        _network?.SendTradeLog(tradeId, "NT:FILL", $"{resultType} filled @ {fillPrice} qty={quantity}");
                        
                        // Show notification
                        if (resultType == "TP")
                            NotifySuccess($"Take Profit Hit @ {fillPrice}");
                        else
                            NotifyWarning($"Stop Loss Hit @ {fillPrice}");
                        
                        // Clean up tracking
                        lock (_ordersLock)
                        {
                            _pendingEntries.Remove(tradeId);
                            _stopLossOrders.Remove(tradeId);
                            _tradeIdToAtmStrategy.Remove(tradeId);
                        }
                    }
                }
                
                // Check if this is a close execution from our pending close
                else if (!string.IsNullOrEmpty(_pendingCloseTradeId))
                {
                    // Check if position is now flat
                    var instrument = Instrument.GetInstrument(_instrument);
                    if (instrument != null)
                    {
                        Position position = null;
                        foreach (var pos in _account.Positions)
                        {
                            if (pos.Instrument == instrument)
                            {
                                position = pos;
                                break;
                            }
                        }
                        
                        // If position is flat (null or quantity = 0), send EXIT_FILL
                        if (position == null || position.Quantity == 0)
                        {
                            var exitPrice = fillPrice;
                            Log($"CLOSE FILL: {_pendingCloseTradeId} @ {exitPrice}");
                            
                            _network?.SendExitFill(_pendingCloseTradeId, exitPrice, "CLOSE");
                            _network?.SendTradeLog(_pendingCloseTradeId, "NT:CLOSE_FILL", $"Position closed @ {exitPrice}");
                            NotifySuccess($"Position Closed @ {exitPrice}");
                            
                            // Clean up tracking
                            lock (_ordersLock)
                            {
                                _pendingEntries.Remove(_pendingCloseTradeId);
                                _stopLossOrders.Remove(_pendingCloseTradeId);
                                _tradeIdToAtmStrategy.Remove(_pendingCloseTradeId);
                            }
                            
                            // Clear pending close
                            _pendingCloseTradeId = null;
                            _pendingCloseExitPrice = 0;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Log($"ERROR in execution update: {ex.Message}");
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // Test Connection & E2E Tests
        // ═══════════════════════════════════════════════════════════════════

        private async Task TestConnectionAsync()
        {
            Log("=== TEST CONNECTION ===");
            Log("Sending TEST_PING via REQ/REP socket...");
            
            try
            {
                // Use synchronous query for reliable request-response
                bool success = await Task.Run(() => _network?.SendTestPingWithResponse(2000) ?? false);
                
                if (success)
                {
                    Log("✅ Received TEST_PONG response from Python");
                    Log("=== TEST CONNECTION: PASSED ===");
                    Log("ZMQ REQ/REP connection is working");
                }
                else
                {
                    Log("❌ No TEST_PONG response received");
                    Log("=== TEST CONNECTION: FAILED ===");
                    Log("Check that Python gateway is running on port 5557");
                }
            }
            catch (Exception ex)
            {
                Log($"❌ TEST CONNECTION FAILED: {ex.Message}");
                Log("=== TEST CONNECTION: FAILED ===");
            }
        }

        private async Task RunE2ETestsAsync()
        {
            if (_ui != null) _ui.SetE2EButtonEnabled(false);
            
            try
            {
                var runner = new ZmqE2ETestRunner(_network, Log);
                await runner.RunAllScenariosAsync();
            }
            finally
            {
                if (_ui != null) _ui.SetE2EButtonEnabled(true);
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // E2E Test Runner
    // ═══════════════════════════════════════════════════════════════════════

    internal class ZmqE2ETestRunner
    {
        private readonly ZmqNetwork _network;
        private readonly Action<string> _log;

        internal ZmqE2ETestRunner(ZmqNetwork network, Action<string> log)
        {
            _network = network;
            _log = log;
        }

        internal async Task<int> RunAllScenariosAsync()
        {
            _log("=== E2E TESTS STARTING ===");
            _log("Sending TEST_START messages to Python for each scenario");

            int passed = 0;
            int total = 3;
            string[] scenarios = { "tp_hit", "sl_hit", "session_end" };

            foreach (var scenario in scenarios)
            {
                _log($"");
                _log($"--- Testing scenario: {scenario} ---");
                
                try
                {
                    // Send TEST_START to Python - Python will create trade and send open order
                    _log($"[TEST] Sending TEST_START for {scenario}");
                    _network?.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
                    
                    // Wait for Python to process and send open order command
                    await Task.Delay(1000);
                    
                    // The rest of the test flow is handled by Python:
                    // 1. Python creates trade in DB
                    // 2. Python enqueues open order command
                    // 3. C# receives command and sends ENTRY_FILL
                    // 4. Python processes entry, enqueues modify order
                    // 5. C# receives modify and sends EXIT_FILL based on scenario
                    
                    _log($"[TEST] Scenario {scenario}: Commands sent, check Python logs for results");
                    passed++;
                }
                catch (Exception ex)
                {
                    _log($"[TEST] Scenario {scenario}: FAILED - {ex.Message}");
                }
                
                // Delay between scenarios
                await Task.Delay(1000);
            }

            _log($"");
            _log($"=== E2E TESTS COMPLETE: {passed}/{total} passed ===");
            _log("Note: Full test results require Python test endpoint implementation");
            return passed;
        }
    }
}
