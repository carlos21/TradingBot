// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Configuration Loader
// Loads ZmqConfiguration from an optional JSON file so users can toggle
// auto-connect without recompiling the AddOn.
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.IO;
using Newtonsoft.Json;

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
                instrument: "MNQ 06-26",
                historyDays: 30,
                batchSize: 500,
                maxTicksPerSecond: 10,
                platformVersion: "2.0.0-refactored",
                autoConnectOnStartup: false,
                autoShowWindow: true
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
                    instrument: dto.instrument ?? defaults.Instrument,
                    historyDays: dto.historyDays ?? defaults.HistoryDays,
                    batchSize: dto.batchSize ?? defaults.BatchSize,
                    maxTicksPerSecond: dto.maxTicksPerSecond ?? defaults.MaxTicksPerSecond,
                    platformVersion: dto.platformVersion ?? defaults.PlatformVersion,
                    autoConnectOnStartup: dto.autoConnectOnStartup ?? defaults.AutoConnectOnStartup,
                    autoShowWindow: dto.autoShowWindow ?? defaults.AutoShowWindow
                );
            }
            catch (Exception ex)
            {
                // Silently fall back to defaults so compilation never breaks.
                // The error will be visible in NinjaTrader's Output window at runtime.
                System.Diagnostics.Debug.WriteLine($"[TradingBotZMQ] Config load failed: {ex.Message}");
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
            public string instrument;
            public int? historyDays;
            public int? batchSize;
            public int? maxTicksPerSecond;
            public string platformVersion;
            public bool? autoConnectOnStartup;
            public bool? autoShowWindow;
        }
    }
}
