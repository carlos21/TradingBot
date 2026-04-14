// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Serializers
// Concrete implementations of serialization contracts
// ═══════════════════════════════════════════════════════════════════════

using System;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// JSON implementation of IMessageSerializer.
    /// Uses Newtonsoft.Json - can be swapped for System.Text.Json or MessagePack.
    /// </summary>
    internal sealed class JsonMessageSerializer : IMessageSerializer
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
