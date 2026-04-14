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
        private readonly Func<int, Task> _sendHistoryFunc;

        public RefreshRequestHandler(ZmqNetwork network, ILogger logger, Func<int, Task> sendHistoryFunc)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _sendHistoryFunc = sendHistoryFunc ?? throw new ArgumentNullException(nameof(sendHistoryFunc));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var days = payload?["days"]?.Value<int>() ?? 1;
                _logger.Info($"REFRESH REQUEST: {days} days");
                _ = _sendHistoryFunc(days).ContinueWith(t =>
                {
                    if (t.IsFaulted) _logger.Error("Refresh history send failed", t.Exception?.GetBaseException());
                }, TaskContinuationOptions.OnlyOnFaulted);
            }
            catch (Exception ex)
            {
                _logger.Error("Refresh request failed", ex);
                _network?.SendError("ninjatrader", "refresh_failed", ex.Message);
            }
        }
    }
}
