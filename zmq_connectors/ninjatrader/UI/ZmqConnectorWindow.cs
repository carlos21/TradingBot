// ═══════════════════════════════════════════════════════════════════════
// Presentation Layer: ZMQ Connector Window
// MVVM-inspired UI - separates view logic from business logic
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// MVVM-inspired UI layer - separates view logic from business logic.
    /// Uses Action callbacks for loose coupling with the controller.
    /// </summary>
    internal sealed class ZmqConnectorWindow
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
            _log = log ?? throw new ArgumentNullException(nameof(log));
        }

        internal void SetButtonHandlers(Action onConnect, Action onTestConnection = null, Action onE2ETests = null)
        {
            _onConnect = onConnect;
            _onTestConnection = onTestConnection;
            _onE2ETests = onE2ETests;
        }

        internal void Show(bool connected)
        {
            if (_window != null) { _window.Activate(); return; }

            _window = new Window
            {
                Title = "Liquid ZMQ Connector",
                Width = 550,
                Height = 450,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                Background = new SolidColorBrush(Color.FromRgb(30, 30, 30)),
            };

            var grid = new Grid();
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

            // Status
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

            // Stats
            _statsLabel = new TextBlock
            {
                Text = "ZeroMQ Edition - High Performance",
                Foreground = Brushes.Silver,
                FontSize = 12,
                Margin = new Thickness(12, 0, 12, 8),
            };
            Grid.SetRow(_statsLabel, 1);
            grid.Children.Add(_statsLabel);

            // Buttons
            var btnPanel = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(12, 4, 12, 8) };
            _connectBtn = CreateButton(connected ? "Disconnect" : "Connect", () => _onConnect?.Invoke());
            _testConnBtn = CreateButton("Test Connection", () => _onTestConnection?.Invoke(), !connected);
            _e2eTestBtn = CreateButton("Run E2E Tests", () => _onE2ETests?.Invoke(), !connected);
            btnPanel.Children.Add(_connectBtn);
            btnPanel.Children.Add(_testConnBtn);
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
            _window.Closed += (s, ev) => ClearReferences();
            _window.Show();
            Log("Window opened. Click Connect to start ZMQ connection.");
        }

        private Button CreateButton(string text, Action onClick, bool disabled = false)
        {
            var btn = new Button
            {
                Content = text,
                Width = 120,
                Height = 30,
                Margin = new Thickness(0, 0, 8, 0),
                IsEnabled = !disabled
            };
            btn.Click += (s, e) => onClick?.Invoke();
            return btn;
        }

        private void ClearReferences()
        {
            _window = null;
            _logBox = null;
            _statusLabel = null;
            _statsLabel = null;
            _connectBtn = null;
            _testConnBtn = null;
            _e2eTestBtn = null;
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
}
