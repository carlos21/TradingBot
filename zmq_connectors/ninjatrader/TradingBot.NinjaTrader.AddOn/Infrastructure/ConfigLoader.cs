using System;
using System.IO;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public static class ConfigLoader
    {
        public static ZmqConfiguration Load(string path = null)
        {
            try
            {
                path = path ?? GetDefaultConfigPath();
                if (!File.Exists(path))
                    return new ZmqConfiguration();

                var json = File.ReadAllText(path);
                var obj = JObject.Parse(json);

                return new ZmqConfiguration(
                    host: obj["host"]?.Value<string>() ?? "127.0.0.1",
                    marketPort: obj["marketPort"]?.Value<int>() ?? 5555,
                    commandPort: obj["commandPort"]?.Value<int>() ?? 5556,
                    queryPort: obj["queryPort"]?.Value<int>() ?? 5557,
                    heartbeatPort: obj["heartbeatPort"]?.Value<int>() ?? 5558,
                    instrument: obj["instrument"]?.Value<string>() ?? "",
                    historyDays: obj["historyDays"]?.Value<int>() ?? 30,
                    batchSize: obj["batchSize"]?.Value<int>() ?? 500,
                    maxTicksPerSecond: obj["maxTicksPerSecond"]?.Value<int>() ?? 10,
                    platformVersion: obj["platformVersion"]?.Value<string>() ?? "2.0.0",
                    autoConnectOnStartup: obj["autoConnectOnStartup"]?.Value<bool>() ?? false,
                    autoShowWindow: obj["autoShowWindow"]?.Value<bool>() ?? true,
                    logDirectory: obj["logDirectory"]?.Value<string>(),
                    enableFileLogging: obj["enableFileLogging"]?.Value<bool>() ?? true
                );
            }
            catch (Exception)
            {
                return new ZmqConfiguration();
            }
        }

        private static string GetDefaultConfigPath()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            return Path.Combine(documents, "NinjaTrader 8", "bin", "Custom", "TradingBotZmqConfig.json");
        }
    }
}
