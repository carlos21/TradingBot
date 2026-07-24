using System;
using System.Threading;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application.Handlers
{
    public class DisconnectHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IConnectorService _connectorService;
        private readonly DisconnectHandler _handler;

        public DisconnectHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _connectorService = Substitute.For<IConnectorService>();
            _handler = new DisconnectHandler(_network, _logger, _connectorService);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "connectorService")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var connectorService = nullIndex == 2 ? null : _connectorService;

            Action act = () => new DisconnectHandler(network, logger, connectorService);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_Disconnect()
        {
            _handler.CommandType.Should().Be(MessageType.Disconnect);
        }

        [Fact]
        public void Handle_DisconnectsService_ReturnsTrue_AndLogsReason()
        {
            var disconnected = new ManualResetEventSlim(false);
            _connectorService.When(x => x.Disconnect(Arg.Any<string>())).Do(_ => disconnected.Set());

            var payload = new JObject { ["reason"] = "stream stopped" };
            var result = _handler.Handle(payload);

            // Returning true is what makes the command loop send the success ACK
            // (same convention as SubscribeHandler).
            result.Should().BeTrue();
            disconnected.Wait(TimeSpan.FromSeconds(2)).Should().BeTrue("the handler defers Disconnect to a background thread");
            _connectorService.Received(1).Disconnect("Python requested disconnect");
            _logger.Infos.Should().Contain(s => s.Contains("DISCONNECT command: stream stopped"));
        }

        [Fact]
        public void Handle_LogsMissingReason()
        {
            var disconnected = new ManualResetEventSlim(false);
            _connectorService.When(x => x.Disconnect(Arg.Any<string>())).Do(_ => disconnected.Set());

            var result = _handler.Handle(new JObject());

            result.Should().BeTrue();
            disconnected.Wait(TimeSpan.FromSeconds(2)).Should().BeTrue();
            _logger.Infos.Should().Contain(s => s.Contains("DISCONNECT command: <no reason>"));
        }
    }
}
