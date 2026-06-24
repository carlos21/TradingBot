using System;
using System.IO;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Immutable configuration for ZMQ connector.
    /// </summary>
    public sealed class ZmqConfiguration
    {
        public string Host { get; }
        public int MarketPort { get; }
        public int CommandPort { get; }
        public int QueryPort { get; }
        public int HeartbeatPort { get; }
        public string Instrument { get; }
        public int HistoryDays { get; }
        public int BatchSize { get; }
        public int MaxTicksPerSecond { get; }
        public string PlatformVersion { get; }
        public bool AutoConnectOnStartup { get; }
        public bool AutoShowWindow { get; }
        public string LogDirectory { get; }
        public bool EnableFileLogging { get; }

        public string MarketDataAddress => $"tcp://{Host}:{MarketPort}";
        public string CommandAddress => $"tcp://{Host}:{CommandPort}";
        public string QueryAddress => $"tcp://{Host}:{QueryPort}";
        public string HeartbeatAddress => $"tcp://{Host}:{HeartbeatPort}";

        public ZmqConfiguration(
            string host = "127.0.0.1",
            int marketPort = 5555,
            int commandPort = 5556,
            int queryPort = 5557,
            int heartbeatPort = 5558,
            string instrument = "",
            int historyDays = 30,
            int batchSize = 500,
            int maxTicksPerSecond = 10,
            string platformVersion = "2.0.0",
            bool autoConnectOnStartup = false,
            bool autoShowWindow = true,
            string logDirectory = null,
            bool enableFileLogging = true)
        {
            Host = host ?? throw new ArgumentNullException(nameof(host));
            MarketPort = marketPort;
            CommandPort = commandPort;
            QueryPort = queryPort;
            HeartbeatPort = heartbeatPort;
            Instrument = instrument ?? throw new ArgumentNullException(nameof(instrument));
            HistoryDays = historyDays;
            BatchSize = batchSize;
            MaxTicksPerSecond = maxTicksPerSecond;
            PlatformVersion = platformVersion ?? throw new ArgumentNullException(nameof(platformVersion));
            AutoConnectOnStartup = autoConnectOnStartup;
            AutoShowWindow = autoShowWindow;
            LogDirectory = logDirectory ?? GetDefaultLogDirectory();
            EnableFileLogging = enableFileLogging;
        }

        public ZmqConfiguration WithInstrument(string instrument) =>
            new ZmqConfiguration(Host, MarketPort, CommandPort, QueryPort, HeartbeatPort,
                instrument, HistoryDays, BatchSize, MaxTicksPerSecond, PlatformVersion,
                AutoConnectOnStartup, AutoShowWindow, LogDirectory, EnableFileLogging);

        private static string GetDefaultLogDirectory()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            return Path.Combine(documents, "NinjaTrader 8", "bin", "Custom", "logs");
        }
    }
}
