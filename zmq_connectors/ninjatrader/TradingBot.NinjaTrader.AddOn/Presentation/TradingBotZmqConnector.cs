using System;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using TradingBot.NinjaTrader.AddOn.Infrastructure;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Presentation
{
    public class TradingBotZmqConnector : AddOnBase
    {
        private readonly ZmqConfiguration _config;
        private ConnectorService _service;
        private ILogger _logger;
        private FileLogger _fileLogger;
        private ZmqConnectorWindow _ui;

        private MenuItem _menuItem;
        private MenuItem _existingNewMenu;
        private System.Windows.Threading.DispatcherTimer _statsTimer;

        public TradingBotZmqConnector()
        {
            _config = ConfigLoader.Load();
        }

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
            _existingNewMenu = FindMenuItem(cc.MainMenu, "New");
            if (_existingNewMenu == null) return;

            _menuItem = new MenuItem { Header = "Liquid ZMQ Connector" };
            _menuItem.Click += OnMenuItemClick;
            _existingNewMenu.Items.Add(_menuItem);

            if (_config.AutoConnectOnStartup)
            {
                try
                {
                    if (_config.AutoShowWindow)
                        ShowStatusWindow();
                    else
                        InitializeLoggerOnly();

                    System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(new Action(() =>
                    {
                        if (_service == null || !_service.IsConnected)
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
                var uiLogger = new NinjatraderLogger(msg => _ui.Log(msg));
                _logger = CreateCompositeLogger(uiLogger);
                _ui.SetButtonHandlers(
                    onConnect: ToggleConnection,
                    onTestConnection: () => _ = _service?.TestConnectionAsync(),
                    onE2ETests: () => _ = RunE2ETestsAsync()
                );
            }
            _ui.Show(_service?.IsConnected ?? false);
        }

        private void InitializeLoggerOnly()
        {
            if (_logger == null)
            {
                var outputLogger = new NinjatraderLogger(msg =>
                    Print("[ZMQ] " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + msg));
                _logger = CreateCompositeLogger(outputLogger);
            }
        }

        private ILogger CreateCompositeLogger(ILogger primarySink)
        {
            if (!_config.EnableFileLogging)
                return primarySink;

            if (_fileLogger == null)
                _fileLogger = new FileLogger(_config.LogDirectory);
            return new CompositeLogger(primarySink, _fileLogger);
        }

        private void ToggleConnection()
        {
            if (_service?.IsConnected ?? false)
                Disconnect("user requested");
            else
                Connect();
        }

        private void Connect()
        {
            try
            {
                if (_logger == null)
                    InitializeLoggerOnly();

                bool simulate = _ui?.IsSimulateTradesEnabled ?? false;
                _service = NinjaTraderCompositionRoot.Build(_config, _logger, simulate);

                WireAccountEvents();
                _service.Connect();
                StartStatsTimer();
            }
            catch (Exception ex)
            {
                _logger?.Error("Connect failed", ex);
            }
        }

        private void Disconnect(string reason)
        {
            try
            {
                StopStatsTimer();
                UnwireAccountEvents();
                _service?.Disconnect(reason);
                _service?.Dispose();
                _service = null;
                _ui?.UpdateStatus(false, "");
            }
            catch (Exception ex)
            {
                _logger?.Error("Disconnect failed", ex);
            }
        }

        private void WireAccountEvents()
        {
            foreach (var acct in Account.All)
            {
                acct.ExecutionUpdate += OnExecutionUpdate;
                acct.OrderUpdate += OnOrderUpdate;
            }
        }

        private void UnwireAccountEvents()
        {
            foreach (var acct in Account.All)
            {
                try { acct.ExecutionUpdate -= OnExecutionUpdate; } catch { }
                try { acct.OrderUpdate -= OnOrderUpdate; } catch { }
            }
        }

        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            _service?.OnOrderUpdate(BrokerOrderMapper.ToBrokerOrder(e.Order));
        }

        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            _service?.OnExecutionUpdate(
                BrokerOrderMapper.ToBrokerOrder(e.Execution.Order),
                e.Execution.Price,
                e.Execution.Quantity);
        }

        private void StartStatsTimer()
        {
            StopStatsTimer();
            _statsTimer = new System.Windows.Threading.DispatcherTimer(
                TimeSpan.FromSeconds(1),
                System.Windows.Threading.DispatcherPriority.Background,
                (s, e) => _ui?.UpdateStatus(_service?.IsConnected ?? false, _service?.GetStats() ?? ""),
                System.Windows.Threading.Dispatcher.CurrentDispatcher);
            _statsTimer.Start();
        }

        private void StopStatsTimer()
        {
            if (_statsTimer != null)
            {
                _statsTimer.Stop();
                _statsTimer = null;
            }
        }

        private async System.Threading.Tasks.Task RunE2ETestsAsync()
        {
            if (_service == null) return;
            _ui?.SetE2EButtonEnabled(false);
            try
            {
                var runner = new ZmqE2ETestRunner(
                    _service.Network,
                    _logger,
                    new NtAccountProvider(),
                    new SimulationTradingMode(true));
                await runner.RunAllScenariosAsync();
            }
            finally
            {
                _ui?.SetE2EButtonEnabled(true);
            }
        }

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
