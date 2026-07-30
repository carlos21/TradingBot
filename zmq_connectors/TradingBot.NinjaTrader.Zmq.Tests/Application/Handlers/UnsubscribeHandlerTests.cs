using System;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using NSubstitute.ExceptionExtensions;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application.Handlers
{
    public class UnsubscribeHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly UnsubscribeHandler _handler;

        public UnsubscribeHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            _handler = new UnsubscribeHandler(_network, _logger, _streamingCoordinator);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "streamingCoordinator")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var streamingCoordinator = nullIndex == 2 ? null : _streamingCoordinator;

            Action act = () => new UnsubscribeHandler(network, logger, streamingCoordinator);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_Unsubscribe()
        {
            _handler.CommandType.Should().Be(MessageType.Unsubscribe);
        }

        [Fact]
        public void Handle_ReturnsTrue_WhenCoordinatorStops()
        {
            _streamingCoordinator.Stop("MNQ 09-25").Returns(true);

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeTrue();
            _streamingCoordinator.Received(1).Stop("MNQ 09-25");
            _logger.Infos.Should().Contain(s => s.Contains("UNSUBSCRIBE command: MNQ 09-25"));
        }

        [Fact]
        public void Handle_ReturnsFalse_WhenInstrumentNotSubscribed()
        {
            _streamingCoordinator.Stop("MNQ 09-25").Returns(false);

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeFalse();
            _streamingCoordinator.Received(1).Stop("MNQ 09-25");
        }

        [Fact]
        public void Handle_ReturnsFalse_AndSkipsCoordinator_WhenInstrumentMissing()
        {
            var result = _handler.Handle(new JObject());

            result.Should().BeFalse();
            _streamingCoordinator.DidNotReceive().Stop(Arg.Any<string>());
            _logger.Infos.Should().Contain(s => s.Contains("UNSUBSCRIBE command: <missing>"));
        }

        [Fact]
        public void Handle_ReturnsFalse_AndSkipsCoordinator_WhenInstrumentEmpty()
        {
            var result = _handler.Handle(TestDataFactory.SubscribePayload(""));

            result.Should().BeFalse();
            _streamingCoordinator.DidNotReceive().Stop(Arg.Any<string>());
        }

        [Fact]
        public void Handle_ReturnsFalse_WhenCoordinatorThrows()
        {
            _streamingCoordinator.Stop(Arg.Any<string>()).Throws(new InvalidOperationException("coordinator error"));

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "unsubscribe_failed", "coordinator error");
        }
    }
}
