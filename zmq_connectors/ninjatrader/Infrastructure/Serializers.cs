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

        public JsonMessageSerializer()
        {
            _settings = new JsonSerializerSettings
            {
                NullValueHandling = NullValueHandling.Ignore,
                DefaultValueHandling = DefaultValueHandling.Ignore
            };
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
            catch
            {
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
