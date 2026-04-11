// TradingBotZmqConnector.cs — NinjaScript AddOn (ZeroMQ Edition)
// High-performance ZeroMQ connector for Python TradingBot.
//
// Installation:
//   1. Install NetMQ via NinjaTrader's NuGet package manager
//   2. In NinjaTrader: New > NinjaScript Editor > right-click AddOns > Add new file
//   3. Paste this code, compile (F5)
//   4. Click New > TradingBot ZMQ Connector in the Control Center
//
// Protocol: tcp://127.0.0.1:5555-5558 (PUB/SUB, PUSH/PULL, REQ/REP)

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Text;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using NetMQ;
using NetMQ.Sockets;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

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
        private readonly NetMQContext _context;
        private PublisherSocket _marketPub;      // PUB: Send market data
        private PullSocket _commandPull;         // PULL: Receive commands
        private ResponseSocket _queryRep;        // REP: Handle queries
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
            
            _context = NetMQContext.Create();
        }
        
        public void Start()
        {
            // PUB socket: Send market data to Python
            _marketPub = _context.CreatePublisherSocket();
            _marketPub.Connect(_marketDataAddr);
            
            // PULL socket: Receive commands from Python
            _commandPull = _context.CreatePullSocket();
            _commandPull.Connect(_commandAddr);
            
            // REP socket: Handle queries from Python
            _queryRep = _context.CreateResponseSocket();
            _queryRep.Connect(_queryAddr);
            
            // PUB socket: Send heartbeats
            _heartbeatPub = _context.CreatePublisherSocket();
            _heartbeatPub.Connect(_heartbeatAddr);
        }
        
        public void Stop()
        {
            _marketPub?.Dispose();
            _commandPull?.Dispose();
            _queryRep?.Dispose();
            _heartbeatPub?.Dispose();
            
            _marketPub = null;
            _commandPull = null;
            _queryRep = null;
            _heartbeatPub = null;
        }
        
        public void Dispose()
        {
            Stop();
            _context?.Dispose();
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
            if (_queryRep == null) return null;
            
            if (_queryRep.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out string message))
            {
                return message;
            }
            return null;
        }
        
        public void SendQueryResponse(JObject payload)
        {
            if (_queryRep == null) return;
            
            var envelope = MessageEnvelope.Create(MessageType.PositionResponse, payload, NextSeq());
            _queryRep.SendFrame(envelope.ToJson());
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
        
        private Action _onConnect;
        
        internal ZmqConnectorWindow(Action<string> log)
        {
            _log = log;
        }
        
        internal void SetConnectHandler(Action onConnect)
        {
            _onConnect = onConnect;
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
                Width = 520,
                Height = 400,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                Background = new SolidColorBrush(Color.FromRgb(30, 30, 30)),
            };
            
            var grid = new Grid();
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            
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
            };
            _connectBtn.Click += (s, e) => { _onConnect?.Invoke(); };
            btnPanel.Children.Add(_connectBtn);
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
        private MarketDataPipeline _marketData;
        private OrderManager _orders;
        
        // Menu
        private NTMenuItem _menuItem;
        private NTMenuItem _existingNewMenu;
        
        // Stats
        private long _commandsReceived = 0;
        private long _ticksSent = 0;
        private long _barsSent = 0;

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
            
            _existingNewMenu = cc.FindFirst("ControlCenterMenuItemNew") as NTMenuItem;
            if (_existingNewMenu == null) return;
            
            _menuItem = new NTMenuItem 
            { 
                Header = "TradingBot ZMQ Connector",
                Style = Application.Current.TryFindResource("MainMenuItem") as Style 
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
        
        private void OnMenuItemClick(object sender, RoutedEventArgs e)
        {
            ShowStatusWindow();
        }
        
        private void ShowStatusWindow()
        {
            if (_ui == null)
            {
                _ui = new ZmqConnectorWindow(msg => Print("[ZMQ] " + msg));
                _ui.SetConnectHandler(() =>
                {
                    if (_connected) Disconnect();
                    else Connect();
                });
            }
            _ui.Show(_connected);
        }
        
        private void Log(string message)
        {
            _ui?.Log(message);
            Print("[ZMQ] " + message);
        }
        
        private void UpdateStats()
        {
            var stats = $"Ticks: {_ticksSent} | Bars: {_barsSent} | Cmds: {_commandsReceived}";
            _ui?.UpdateStatus(_connected, stats);
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
                Log($"Connection error: {ex.Message}");
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
                    Log($"Command loop error: {ex.Message}");
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
                    Log($"Heartbeat error: {ex.Message}");
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
                    
                default:
                    Log($"Unknown command: {envelope.MsgType}");
                    break;
            }
        }
        
        private void HandleOpenOrder(JObject payload)
        {
            var tradeId = payload["trade_id"]?.ToString();
            var direction = payload["direction"]?.ToString();
            var entryPrice = payload["entry_price"]?.Value<double>() ?? 0;
            var stopLoss = payload["stop_loss"]?.Value<double>() ?? 0;
            var takeProfit = payload["take_profit"]?.Value<double>() ?? 0;
            var slPoints = payload["risk_points"]?.Value<double>() ?? 0;
            
            Log($"OPEN ORDER: {tradeId} {direction} @ {entryPrice} SL={stopLoss} TP={takeProfit}");
            
            // TODO: Implement actual order placement via ATM strategy
            // For now, send confirmation
            _network?.SendEntryFill(tradeId, entryPrice, stopLoss, takeProfit);
        }
        
        private void HandleCloseOrder(JObject payload)
        {
            var tradeId = payload["trade_id"]?.ToString();
            Log($"CLOSE ORDER: {tradeId}");
            
            // TODO: Implement position flattening
            _network?.SendExitFill(tradeId, 0, "CLOSE");
        }
        
        private void HandleModifyOrder(JObject payload)
        {
            var tradeId = payload["trade_id"]?.ToString();
            var newSl = payload["stop_loss"]?.Value<double>();
            Log($"MODIFY ORDER: {tradeId} new SL={newSl}");
            
            // TODO: Implement ATM strategy modification
        }
        
        private void HandleRefreshRequest(JObject payload)
        {
            var days = payload["days"]?.Value<int>() ?? 1;
            Log($"REFRESH REQUEST: {days} days");
            
            _ = SendHistoryAsync(days);
        }

        // ═══════════════════════════════════════════════════════════════════
        // Market Data
        // ═══════════════════════════════════════════════════════════════════

        private async System.Threading.Tasks.Task SendHistoryAsync(int days = 30)
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
                    if (errorCode != ErrorCode.NoError)
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
                Log($"SendHistory error: {ex.Message}");
            }
        }
        
        private void SubscribeToMarketData()
        {
            var instrument = Instrument.GetInstrument(_instrument);
            if (instrument == null)
            {
                Log($"ERROR: Cannot subscribe, instrument '{_instrument}' not found");
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
            if (!_connected || e.MarketDataType != MarketDataType.Last) return;
            
            var utcTs = (long)(e.Time.ToUniversalTime() - new DateTime(1970, 1, 1)).TotalSeconds;
            var pair = e.Instrument.MasterInstrument.Name;
            
            _network?.SendTick(pair, e.Price, (long)e.Volume, e.Time);
            _ticksSent++;
            
            if (_ticksSent % 500 == 0)
                UpdateStats();
        }

        // ═══════════════════════════════════════════════════════════════════
        // Account / Orders
        // ═══════════════════════════════════════════════════════════════════

        private void InitializeAccount()
        {
            if (Account.All.Count == 0)
            {
                Log("WARNING: No trading accounts found");
                return;
            }
            
            _account = Account.All[0];
            _account.ExecutionUpdate += OnExecutionUpdate;
            _account.OrderUpdate += OnOrderUpdate;
            
            Log($"Using account: {_account.Name}");
        }
        
        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            var order = e.Order;
            Log($"ORDER UPDATE: {order.Name} state={order.OrderState}");
        }
        
        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            var execution = e.Execution;
            Log($"EXECUTION: {execution.Order.Name} @ {execution.Price}");
        }
    }
}
