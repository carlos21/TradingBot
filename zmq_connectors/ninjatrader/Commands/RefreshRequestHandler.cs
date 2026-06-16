// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: RefreshRequestHandler
// Handles REFRESH_REQUEST commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles REFRESH_REQUEST commands.
    /// </summary>
    internal sealed class RefreshRequestHandler : ICommandHandler
    {
        public string CommandType => MessageType.RefreshRequest;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Func<string, int, Task> _sendHistoryFunc;

        public RefreshRequestHandler(ZmqNetwork network, ILogger logger, Func<string, int, Task> sendHistoryFunc)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _sendHistoryFunc = sendHistoryFunc ?? throw new ArgumentNullException(nameof(sendHistoryFunc));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var days = payload?["days"]?.Value<int>() ?? 1;
                var instrument = payload?["instrument"]?.Value<string>();
                if (string.IsNullOrWhiteSpace(instrument))
                {
                    _logger.Error("REFRESH REQUEST: missing instrument in payload");
                    return false;
                }
                _logger.Info($"REFRESH REQUEST: {days} days, instrument={instrument}");
                _ = _sendHistoryFunc(instrument, days).ContinueWith(t =>
                {
                    if (t.IsFaulted) _logger.Error("Refresh history send failed", t.Exception?.GetBaseException());
                }, TaskContinuationOptions.OnlyOnFaulted);
                return true;
            }
            catch (Exception ex)
            {
                _logger.Error("Refresh request failed", ex);
                _network?.SendError("ninjatrader", "refresh_failed", ex.Message);
                return false;
            }
        }
    }
}
