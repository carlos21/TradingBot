// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: SubscribeHandler
// Handles SUBSCRIBE commands from Python (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles SUBSCRIBE commands.
    /// Python sends the full instrument name here; the connector then
    /// subscribes to market data and live bars for that instrument.
    /// </summary>
    internal sealed class SubscribeHandler : ICommandHandler
    {
        public string CommandType => MessageType.Subscribe;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IStreamingCoordinator _streamingCoordinator;

        public SubscribeHandler(ZmqNetwork network, ILogger logger, IStreamingCoordinator streamingCoordinator)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _streamingCoordinator = streamingCoordinator ?? throw new ArgumentNullException(nameof(streamingCoordinator));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var instrument = payload?["instrument"]?.Value<string>();
                _logger.Info($"SUBSCRIBE command: {instrument ?? "<missing>"}");
                return _streamingCoordinator.Start(instrument);
            }
            catch (Exception ex)
            {
                _logger.Error("Subscribe command failed", ex);
                _network?.SendError("ninjatrader", "subscribe_failed", ex.Message);
                return false;
            }
        }
    }
}
