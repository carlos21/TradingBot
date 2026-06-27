using System;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using NSubstitute.ExceptionExtensions;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    public class CommandDispatcherTests
    {
        private readonly TestLogger _logger;
        private readonly CommandDispatcher _dispatcher;

        public CommandDispatcherTests()
        {
            _logger = new TestLogger();
            _dispatcher = new CommandDispatcher(_logger);
        }

        [Fact]
        public void Constructor_Throws_WhenLoggerIsNull()
        {
            Action act = () => new CommandDispatcher(null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("logger");
        }

        [Fact]
        public void Register_Throws_WhenHandlerIsNull()
        {
            Action act = () => _dispatcher.Register(null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("handler");
        }

        [Fact]
        public void Dispatch_ReturnsTrue_WhenHandlerHandlesCommand()
        {
            var handler = Substitute.For<ICommandHandler>();
            handler.CommandType.Returns("TEST_CMD");
            handler.Handle(Arg.Any<JObject>()).Returns(true);
            _dispatcher.Register(handler);

            var envelope = MessageEnvelope.Create("TEST_CMD", new JObject());
            var result = _dispatcher.Dispatch(envelope);

            result.Should().BeTrue();
            handler.Received(1).Handle(envelope.Payload);
        }

        [Fact]
        public void Dispatch_ReturnsFalse_WhenCommandUnknown()
        {
            var envelope = MessageEnvelope.Create("UNKNOWN", new JObject());
            var result = _dispatcher.Dispatch(envelope);

            result.Should().BeFalse();
            _logger.Warnings.Should().ContainSingle(w => w.Contains("Unknown command: UNKNOWN"));
        }

        [Fact]
        public void Dispatch_ReturnsFalse_WhenHandlerThrows()
        {
            var handler = Substitute.For<ICommandHandler>();
            handler.CommandType.Returns("FAIL_CMD");
            handler.Handle(Arg.Any<JObject>()).Throws(new InvalidOperationException("boom"));
            _dispatcher.Register(handler);

            var envelope = MessageEnvelope.Create("FAIL_CMD", new JObject());
            var result = _dispatcher.Dispatch(envelope);

            result.Should().BeFalse();
            _logger.Errors.Should().ContainSingle(e => e.Message.Contains("FAIL_CMD") && e.Exception.Message == "boom");
        }

        [Fact]
        public void Dispatch_ReturnsFalse_WhenEnvelopeIsNull()
        {
            var result = _dispatcher.Dispatch(null);
            result.Should().BeFalse();
        }

        [Fact]
        public void Register_OverwritesExistingHandler_ForSameCommandType()
        {
            var first = Substitute.For<ICommandHandler>();
            first.CommandType.Returns("CMD");
            first.Handle(Arg.Any<JObject>()).Returns(true);

            var second = Substitute.For<ICommandHandler>();
            second.CommandType.Returns("CMD");
            second.Handle(Arg.Any<JObject>()).Returns(true);

            _dispatcher.Register(first);
            _dispatcher.Register(second);

            _dispatcher.Dispatch(MessageEnvelope.Create("CMD", new JObject()));

            first.DidNotReceive().Handle(Arg.Any<JObject>());
            second.Received(1).Handle(Arg.Any<JObject>());
        }
    }
}
