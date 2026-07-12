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
    public class RefreshRequestHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IConnectorClock _clock;
        private readonly RefreshRequestHandler _handler;
        private readonly ZmqConfiguration _config;

        public RefreshRequestHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _barHistoryService = Substitute.For<IBarHistoryService>();
            _clock = Substitute.For<IConnectorClock>();
            _config = TestDataFactory.Config(historyDays: 3, batchSize: 2);
            _handler = new RefreshRequestHandler(_network, _logger, _instrumentProvider, _barHistoryService, _clock, _config);

            _clock.UtcNow.Returns(new DateTime(2025, 1, 10, 12, 0, 0, DateTimeKind.Utc));
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "instrumentProvider")]
        [InlineData(3, "barHistoryService")]
        [InlineData(4, "clock")]
        [InlineData(5, "config")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var instrumentProvider = nullIndex == 2 ? null : _instrumentProvider;
            var barHistoryService = nullIndex == 3 ? null : _barHistoryService;
            var clock = nullIndex == 4 ? null : _clock;
            var config = nullIndex == 5 ? null : _config;

            Action act = () => new RefreshRequestHandler(network, logger, instrumentProvider, barHistoryService, clock, config);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_RefreshRequest()
        {
            _handler.CommandType.Should().Be(MessageType.RefreshRequest);
        }

        [Fact]
        public async Task HandleAsync_SendsBarsInBatches()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            var bars = Enumerable.Range(0, 5).Select(i => TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(-5 + i))).ToList();
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25", 3));
            await Task.Delay(100); // async handler

            _network.Received(1).SendRefreshStart();
            _network.Received(3).SendHistoryBatch("MNQ", Arg.Any<List<JObject>>(), Arg.Any<int>());
            _network.Received(1).SendHistoryEnd();
            _logger.Infos.Should().Contain(s => s.Contains("Sent 5 historical bars"));
        }

        [Fact]
        public async Task HandleAsync_LogsAndReturns_WhenInstrumentMissing()
        {
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns((BrokerInstrument)null);

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25"));
            await Task.Delay(50);

            _network.DidNotReceive().SendRefreshStart();
            _logger.Errors.Should().Contain(e => e.Message.Contains("Instrument 'MNQ 09-25' not found"));
        }

        [Fact]
        public async Task HandleAsync_LogsAndReturns_WhenPayloadInstrumentMissing()
        {
            _handler.Handle(new JObject());
            await Task.Delay(50);

            _network.DidNotReceive().SendRefreshStart();
            _logger.Errors.Should().Contain(e => e.Message.Contains("Instrument missing"));
        }

        [Fact]
        public async Task HandleAsync_SendsHistoryEnd_WhenNoBars()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25"));
            await Task.Delay(50);

            _network.Received(1).SendHistoryEnd();
            _logger.Warnings.Should().Contain(w => w.Contains("No historical bars found"));
        }

        [Fact]
        public async Task HandleAsync_CapsDaysAt30()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25", 100));
            await Task.Delay(50);

            await _barHistoryService.Received(1).RequestHistoryAsync(instrument,
                _clock.UtcNow.AddDays(-30), _clock.UtcNow);
        }

        [Fact]
        public async Task HandleAsync_UsesConfigDays_WhenPayloadOmitsDays()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(new List<Bar>()));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25"));
            await Task.Delay(50);

            await _barHistoryService.Received(1).RequestHistoryAsync(instrument,
                _clock.UtcNow.AddDays(-3), _clock.UtcNow);
        }

        [Fact]
        public async Task HandleAsync_TrimsBarsToMaxCount()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            // days=0 -> maxBars=0, so all 5 bars should be trimmed away
            var bars = Enumerable.Range(0, 5).Select(i => TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(i))).ToList();
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25", 0));
            await Task.Delay(100);

            _network.DidNotReceiveWithAnyArgs().SendHistoryBatch(null, null, 0);
            _network.Received(1).SendHistoryEnd();
        }

        [Fact]
        public async Task HandleAsync_DoesNotTrim_WhenBarCountBelowMax()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            var bars = Enumerable.Range(0, 5).Select(i => TestDataFactory.Bar(time: _clock.UtcNow.AddMinutes(i))).ToList();
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25", 3));
            await Task.Delay(100);

            _network.Received(3).SendHistoryBatch("MNQ", Arg.Any<List<JObject>>(), 3);
        }

        [Fact]
        public async Task HandleAsync_ReturnsSingleBar_WhenHistoryHasOneBar()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            var bars = new List<Bar> { TestDataFactory.Bar() };
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25", 3));
            await Task.Delay(100);

            _network.Received(1).SendHistoryBatch("MNQ", Arg.Is<List<JObject>>(list => list.Count == 1), 3);
            _network.Received(1).SendHistoryEnd();
        }

        [Fact]
        public async Task HandleAsync_SendsError_WhenHistoryServiceThrows()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromException<IReadOnlyList<Bar>>(new InvalidOperationException("history failed")));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25"));
            await Task.Delay(100);

            _network.Received(1).SendError("ninjatrader", "history_load_failed", Arg.Any<string>(), Arg.Any<string>());
            _logger.Errors.Should().Contain(e => e.Exception.Message == "history failed");
        }
        [Fact]
        public async Task HandleAsync_SendsHistoryEnd_WhenHistoryReturnsNull()
        {
            var instrument = TestDataFactory.Instrument();
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _barHistoryService.RequestHistoryAsync(instrument, Arg.Any<DateTime>(), Arg.Any<DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>((IReadOnlyList<Bar>)null));

            _handler.Handle(TestDataFactory.RefreshPayload("MNQ 09-25"));
            await Task.Delay(100);

            _network.Received(1).SendHistoryEnd();
            _logger.Warnings.Should().Contain(w => w.Contains("No historical bars found"));
        }
    }
}
