using System;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application.Handlers
{
    public class TestStartHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly TestStartHandler _handler;

        public TestStartHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _handler = new TestStartHandler(_network, _logger);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;

            Action act = () => new TestStartHandler(network, logger);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_TestStart()
        {
            _handler.CommandType.Should().Be(MessageType.TestStart);
        }

        [Fact]
        public void Handle_ReturnsTrue_AndLogsScenario()
        {
            var result = _handler.Handle(new JObject { ["scenario"] = "e2e-smoke" });

            result.Should().BeTrue();
            _logger.Infos.Should().Contain(s => s.Contains("TEST START command: e2e-smoke"));
        }

        [Fact]
        public void Handle_UsesUnknownScenario_WhenMissing()
        {
            var result = _handler.Handle(new JObject());

            result.Should().BeTrue();
            _logger.Infos.Should().Contain(s => s.Contains("TEST START command: unknown"));
        }

        [Fact]
        public void Handle_ReturnsTrue_WhenPayloadIsNull()
        {
            // Newtonsoft JObject indexer handles null safely via null-conditional access
            var result = _handler.Handle(null);

            result.Should().BeTrue();
            _logger.Infos.Should().Contain(s => s.Contains("TEST START command: unknown"));
        }

        [Fact]
        public void Handle_ReturnsFalse_WhenScenarioValueThrows()
        {
            var payload = new JObject { ["scenario"] = new JArray("invalid") };

            var result = _handler.Handle(payload);

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "test_start_failed", Arg.Any<string>());
        }
    }
}
