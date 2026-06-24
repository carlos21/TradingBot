using System;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class JsonMessageSerializer : IMessageSerializer
    {
        private readonly JsonSerializerSettings _settings;
        private readonly ILogger _logger;

        public JsonMessageSerializer(ILogger logger = null)
        {
            _settings = new JsonSerializerSettings
            {
                NullValueHandling = NullValueHandling.Ignore,
                DefaultValueHandling = DefaultValueHandling.Ignore
            };
            _logger = logger;
        }

        public string Serialize(MessageEnvelope envelope)
        {
            if (envelope == null) throw new ArgumentNullException(nameof(envelope));
            return JsonConvert.SerializeObject(envelope, _settings);
        }

        public MessageEnvelope Deserialize(string json)
        {
            if (string.IsNullOrWhiteSpace(json)) return null;
            try
            {
                return JsonConvert.DeserializeObject<MessageEnvelope>(json);
            }
            catch (Exception ex)
            {
                _logger?.Warning($"JSON deserialization failed: {ex.Message}");
                return null;
            }
        }

        public T DeserializePayload<T>(JObject payload, string key)
        {
            if (payload == null || !payload.TryGetValue(key, out var token))
                return default;
            return token.ToObject<T>();
        }
    }
}
