// ═══════════════════════════════════════════════════════════════════════
// Domain Layer: Value Objects
// Immutable data objects that carry no identity
// ═══════════════════════════════════════════════════════════════════════

using System;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Immutable configuration for ZMQ connector.
    /// Value Object pattern - equality based on values, immutable state.
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
            string instrument = "MNQ 06-26",
            int historyDays = 30,
            int batchSize = 500,
            int maxTicksPerSecond = 3,
            string platformVersion = "2.0.0")
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
        }

        public ZmqConfiguration WithInstrument(string instrument) =>
            new ZmqConfiguration(Host, MarketPort, CommandPort, QueryPort, HeartbeatPort, 
                instrument, HistoryDays, BatchSize, MaxTicksPerSecond, PlatformVersion);
    }

    /// <summary>
    /// Message envelope for all protocol messages.
    /// Immutable after creation - uses factory method pattern.
    /// </summary>
    public sealed class MessageEnvelope
    {
        [JsonProperty("msg_type")]
        public string MsgType { get; private set; }

        [JsonProperty("timestamp")]
        public double Timestamp { get; private set; }

        [JsonProperty("seq_num")]
        public int SeqNum { get; private set; }

        [JsonProperty("payload")]
        public JObject Payload { get; private set; }

        private MessageEnvelope() { }

        public string ToJson() => JsonConvert.SerializeObject(this);

        public static MessageEnvelope Create(string msgType, JObject payload, int seqNum = 0)
        {
            return new MessageEnvelope
            {
                MsgType = msgType ?? throw new ArgumentNullException(nameof(msgType)),
                Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
                SeqNum = seqNum,
                Payload = payload ?? new JObject()
            };
        }
    }

    /// <summary>
    /// Value object: Pending entry information.
    /// Immutable state for entry tracking.
    /// </summary>
    public sealed class PendingEntryInfo
    {
        public string Direction { get; }
        public double SlPoints { get; }
        public double RrRatio { get; }
        public string AtmStrategyName { get; }

        public PendingEntryInfo(string direction, double slPoints, double rrRatio, string atmStrategyName)
        {
            Direction = direction ?? throw new ArgumentNullException(nameof(direction));
            SlPoints = slPoints;
            RrRatio = rrRatio;
            AtmStrategyName = atmStrategyName ?? throw new ArgumentNullException(nameof(atmStrategyName));
        }
    }

    /// <summary>
    /// Event args for tick data (immutable value object).
    /// </summary>
    public sealed class TickEventArgs : EventArgs
    {
        public string Instrument { get; }
        public double Price { get; }
        public long Volume { get; }
        public DateTime Time { get; }
        public double? Bid { get; }
        public double? Ask { get; }

        public TickEventArgs(string instrument, double price, long volume, DateTime time, 
            double? bid = null, double? ask = null)
        {
            Instrument = instrument ?? throw new ArgumentNullException(nameof(instrument));
            Price = price;
            Volume = volume;
            Time = time;
            Bid = bid;
            Ask = ask;
        }
    }
}
