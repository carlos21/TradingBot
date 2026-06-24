using Newtonsoft.Json.Linq;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Strategy pattern: Serialize/deserialize messages.
    /// Allows swapping JSON for other formats without changing business logic.
    /// </summary>
    public interface IMessageSerializer
    {
        string Serialize(MessageEnvelope envelope);
        MessageEnvelope Deserialize(string json);
        T DeserializePayload<T>(JObject payload, string key);
    }
}
