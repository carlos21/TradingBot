using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class SubscribeHandler : ICommandHandler
    {
        public string CommandType => MessageType.Subscribe;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IStreamingCoordinator _streamingCoordinator;

        public SubscribeHandler(IZmqNetwork network, ILogger logger, IStreamingCoordinator streamingCoordinator)
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
                _network.SendError("ninjatrader", "subscribe_failed", ex.Message);
                return false;
            }
        }
    }
}
