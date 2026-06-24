using System;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Message envelope for all protocol messages.
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
}
