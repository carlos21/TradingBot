// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Configuration Loader
// Loads ZmqConfiguration from an optional JSON file so users can toggle
// auto-connect without recompiling the AddOn.
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.IO;
using Newtonsoft.Json;
using NinjaTrader.Code;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Loads ZmqConfiguration from JSON, merging file values over hard-coded defaults.
    /// File path: Documents\NinjaTrader 8\bin\Custom\TradingBotZmqConfig.json
    /// </summary>
    internal static class ConfigLoader
    {
        private static readonly string ConfigFileName = "TradingBotZmqConfig.json";

        /// <summary>
        /// Returns default configuration merged with any values found in the JSON file.
        /// If the file is missing or malformed, returns the hard-coded defaults.
        /// </summary>
        public static ZmqConfiguration Load()
        {
            var defaults = new ZmqConfiguration(
                host: "127.0.0.1",
                marketPort: 5555,
                commandPort: 5556,
                queryPort: 5557,
                heartbeatPort: 5558,
                historyDays: 30,
                batchSize: 500,
                maxTicksPerSecond: 10,
                platformVersion: "2.0.0-refactored",
                autoConnectOnStartup: false,
                autoShowWindow: true,
                logDirectory: null,
                enableFileLogging: true
            );

            string path = GetConfigPath();
            if (!File.Exists(path))
                return defaults;

            try
            {
                string json = File.ReadAllText(path);
                var dto = JsonConvert.DeserializeObject<ZmqConfigDto>(json);
                if (dto == null)
                    return defaults;

                return new ZmqConfiguration(
                    host: dto.host ?? defaults.Host,
                    marketPort: dto.marketPort ?? defaults.MarketPort,
                    commandPort: dto.commandPort ?? defaults.CommandPort,
                    queryPort: dto.queryPort ?? defaults.QueryPort,
                    heartbeatPort: dto.heartbeatPort ?? defaults.HeartbeatPort,
                    historyDays: dto.historyDays ?? defaults.HistoryDays,
                    batchSize: dto.batchSize ?? defaults.BatchSize,
                    maxTicksPerSecond: dto.maxTicksPerSecond ?? defaults.MaxTicksPerSecond,
                    platformVersion: dto.platformVersion ?? defaults.PlatformVersion,
                    autoConnectOnStartup: dto.autoConnectOnStartup ?? defaults.AutoConnectOnStartup,
                    autoShowWindow: dto.autoShowWindow ?? defaults.AutoShowWindow,
                    logDirectory: dto.logDirectory ?? defaults.LogDirectory,
                    enableFileLogging: dto.enableFileLogging ?? defaults.EnableFileLogging
                );
            }
            catch (Exception ex)
            {
                // Log to NT Output window so users can see config errors.
                // Cannot use ILogger here (static context), so use Output.Process on UI thread.
                string msg = $"[TradingBotZMQ] Config load failed: {ex.Message}";
                NinjaTrader.Core.Globals.RandomDispatcher.BeginInvoke(new Action(() =>
                    Output.Process(msg, PrintTo.OutputTab1)));
                return defaults;
            }
        }

        private static string GetConfigPath()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            return Path.Combine(documents, "NinjaTrader 8", "bin", "Custom", ConfigFileName);
        }

        /// <summary>
        /// Mutable DTO used only for JSON deserialization.
        /// All fields are nullable so missing keys fall back to defaults.
        /// </summary>
        private class ZmqConfigDto
        {
            public string host;
            public int? marketPort;
            public int? commandPort;
            public int? queryPort;
            public int? heartbeatPort;
            public int? historyDays;
            public int? batchSize;
            public int? maxTicksPerSecond;
            public string platformVersion;
            public bool? autoConnectOnStartup;
            public bool? autoShowWindow;
            public string logDirectory;
            public bool? enableFileLogging;
        }
    }
}
