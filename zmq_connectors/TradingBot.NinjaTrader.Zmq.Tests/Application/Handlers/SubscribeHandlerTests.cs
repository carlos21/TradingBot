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
    public class SubscribeHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly SubscribeHandler _handler;

        public SubscribeHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            _handler = new SubscribeHandler(_network, _logger, _streamingCoordinator);
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

            Action act = () => new SubscribeHandler(network, logger, streamingCoordinator);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_Subscribe()
        {
            _handler.CommandType.Should().Be(MessageType.Subscribe);
        }

        [Fact]
        public void Handle_ReturnsTrue_WhenCoordinatorStarts()
        {
            _streamingCoordinator.Start("MNQ 09-25").Returns(true);

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeTrue();
            _streamingCoordinator.Received(1).Start("MNQ 09-25");
            _logger.Infos.Should().Contain(s => s.Contains("SUBSCRIBE command: MNQ 09-25"));
        }

        [Fact]
        public void Handle_ReturnsFalse_WhenCoordinatorFails()
        {
            _streamingCoordinator.Start("MNQ 09-25").Returns(false);

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeFalse();
        }

        [Fact]
        public void Handle_ReturnsFalse_WhenCoordinatorThrows()
        {
            _streamingCoordinator.Start(Arg.Any<string>()).Throws(new InvalidOperationException("coordinator error"));

            var result = _handler.Handle(TestDataFactory.SubscribePayload("MNQ 09-25"));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "subscribe_failed", "coordinator error");
        }

        [Fact]
        public void Handle_LogsMissingInstrument()
        {
            _streamingCoordinator.Start(null).Returns(true);

            _handler.Handle(new JObject());

            _logger.Infos.Should().Contain(s => s.Contains("SUBSCRIBE command: <missing>"));
        }
    }
}
