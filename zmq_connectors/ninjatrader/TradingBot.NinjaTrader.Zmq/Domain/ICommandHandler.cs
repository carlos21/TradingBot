using Newtonsoft.Json.Linq;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Observer pattern: Handle incoming commands.
    /// Each command type has its own handler for Single Responsibility.
    /// </summary>
    public interface ICommandHandler
    {
        string CommandType { get; }
        /// <summary>
        /// Handle the command. Returns true on success, false on failure.
        /// </summary>
        bool Handle(JObject payload);
    }
}
