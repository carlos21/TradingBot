using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application.Handlers
{
    public class AuditRequestHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly IConnectorClock _clock;
        private readonly AuditRequestHandler _handler;

        public AuditRequestHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _barHistoryService = Substitute.For<IBarHistoryService>();
            _streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            _clock = Substitute.For<IConnectorClock>();
            _handler = new AuditRequestHandler(_network, _logger, _instrumentProvider, _barHistoryService, _streamingCoordinator, _clock);

            _clock.UtcNow.Returns(new DateTime(2025, 1, 10, 12, 0, 0, DateTimeKind.Utc));
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "instrumentProvider")]
        [InlineData(3, "barHistoryService")]
        [InlineData(4, "streamingCoordinator")]
        [InlineData(5, "clock")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var instrumentProvider = nullIndex == 2 ? null : _instrumentProvider;
            var barHistoryService = nullIndex == 3 ? null : _barHistoryService;
            var streamingCoordinator = nullIndex == 4 ? null : _streamingCoordinator;
            var clock = nullIndex == 5 ? null : _clock;

            Action act = () => new AuditRequestHandler(network, logger, instrumentProvider, barHistoryService, streamingCoordinator, clock);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_AuditRequest()
        {
            _handler.CommandType.Should().Be(MessageType.AuditRequest);
        }

        [Fact]
        public async Task HandleAsync_SendsAuditResponse()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            var bars = Enumerable.Range(0, 3).Select(i => TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(-i))).ToList();
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25", 2));
            await Task.Delay(100);

            _network.Received(1).SendAuditResponse("MNQ", Arg.Is<List<JObject>>(list => list.Count == 2));
            _logger.Infos.Should().Contain(s => s.Contains("AUDIT RESPONSE: sent 2 completed bars"));
        }

        [Fact]
        public async Task HandleAsync_UsesCurrentInstrument_WhenPayloadMissing()
        {
            _streamingCoordinator.CurrentInstrument.Returns("MNQ 09-25");
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.AuditPayload());
            await Task.Delay(50);

            _instrumentProvider.Received(1).GetInstrument("MNQ 09-25");
        }

        [Fact]
        public async Task HandleAsync_LogsError_WhenNoInstrumentAvailable()
        {
            _streamingCoordinator.CurrentInstrument.Returns((string)null);

            _handler.Handle(TestDataFactory.AuditPayload());
            await Task.Delay(50);

            _logger.Errors.Should().Contain(e => e.Message.Contains("missing instrument"));
        }

        [Fact]
        public async Task HandleAsync_LogsError_WhenInstrumentNotFound()
        {
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns((BrokerInstrument)null);

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25"));
            await Task.Delay(50);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Instrument 'MNQ 09-25' not found"));
        }

        [Theory]
        [InlineData(0, 60)]
        [InlineData(-5, 60)]
        [InlineData(6000, 5000)]
        public void HandleAsync_ClampsBarsBack(int input, int expected)
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25", input));

            _logger.Infos.Should().Contain(s => s.Contains($"last {expected} bars"));
        }

        [Fact]
        public async Task HandleAsync_SendsAuditResponse_WithBars()
        {
            var instrument = TestDataFactory.Instrument();
            var bars = new List<Bar>
            {
                TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(-2), open: 100, high: 110, low: 90, close: 105, volume: 50),
                TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(-1), open: 105, high: 115, low: 95, close: 110, volume: 60)
            };
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25", 60));
            await Task.Delay(100);

            _network.Received(1).SendAuditResponse("MNQ", Arg.Is<List<JObject>>(list => list.Count == 2));
        }

        [Fact]
        public async Task HandleAsync_SendsEmptyAuditResponse_WhenNoBars()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25", 60));
            await Task.Delay(100);

            _network.Received(1).SendAuditResponse("MNQ", Arg.Is<List<JObject>>(list => list.Count == 0));
        }

        [Fact]
        public async Task HandleAsync_SendsError_WhenHistoryServiceThrows()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromException<IReadOnlyList<Bar>>(new InvalidOperationException("audit failed")));

            _handler.Handle(TestDataFactory.AuditPayload("MNQ 09-25"));
            await Task.Delay(100);

            _network.Received(1).SendError("ninjatrader", "audit_failed", Arg.Any<string>());
        }
    }
}
