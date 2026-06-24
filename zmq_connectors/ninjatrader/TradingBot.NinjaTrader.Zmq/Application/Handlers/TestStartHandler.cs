using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class TestStartHandler : ICommandHandler
    {
        public string CommandType => MessageType.TestStart;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;

        public TestStartHandler(IZmqNetwork network, ILogger logger)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var scenario = payload?["scenario"]?.Value<string>() ?? "unknown";
                _logger.Info($"TEST START command: {scenario}");
                return true;
            }
            catch (Exception ex)
            {
                _logger.Error("Test start command failed", ex);
                _network.SendError("ninjatrader", "test_start_failed", ex.Message);
                return false;
            }
        }
    }
}
