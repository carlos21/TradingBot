using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class UnsubscribeHandler : ICommandHandler
    {
        public string CommandType => MessageType.Unsubscribe;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IStreamingCoordinator _streamingCoordinator;

        public UnsubscribeHandler(IZmqNetwork network, ILogger logger, IStreamingCoordinator streamingCoordinator)
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
                _logger.Info($"UNSUBSCRIBE command: {instrument ?? "<missing>"}");
                if (string.IsNullOrWhiteSpace(instrument))
                {
                    _logger.Error("Unsubscribe command received with empty instrument");
                    return false;
                }
                return _streamingCoordinator.Stop(instrument);
            }
            catch (Exception ex)
            {
                _logger.Error("Unsubscribe command failed", ex);
                _network.SendError("ninjatrader", "unsubscribe_failed", ex.Message);
                return false;
            }
        }
    }
}
