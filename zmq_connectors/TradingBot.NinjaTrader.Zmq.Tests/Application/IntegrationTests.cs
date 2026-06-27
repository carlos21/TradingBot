using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    public class IntegrationTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly InMemoryOrderTracker _orderTracker;
        private readonly CommandDispatcher _dispatcher;

        public IntegrationTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _tradingMode = Substitute.For<ITradingMode>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _orderExecutionService = Substitute.For<IOrderExecutionService>();
            _orderTracker = new InMemoryOrderTracker();
            _dispatcher = new CommandDispatcher(_logger);

            var account = TestDataFactory.Account(cashValue: 100000);
            var instrument = TestDataFactory.Instrument(pointValue: 1.0);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);

            _dispatcher.Register(new OrderOpenHandler(_network, _logger, _orderTracker, _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService));
            _dispatcher.Register(new OrderCloseHandler(_network, _logger, _orderTracker, Substitute.For<ITradeIdExtractor>(), _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService));
            _dispatcher.Register(new OrderModifyHandler(_network, _logger, _orderTracker, Substitute.For<ITradeIdExtractor>(), _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService));
        }

        [Fact]
        public void OpenModifyCloseFlow_Live_CreatesSubmitsAndClosesOrder()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", state: OrderState.Filled, filled: 2);
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working);
            var closeOrder = TestDataFactory.Order(name: "Close_test-1", side: OrderSide.Sell);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.CreateEntryOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);
            _orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "test-1").Returns(closeOrder);

            // Open
            var openResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(riskUsd: 1000, riskPoints: 10)));
            openResult.Should().BeTrue();
            _orderExecutionService.Received(1).SubmitOrder(entryOrder);

            // Modify
            var modifyResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19990, takeProfit: 20010)));
            modifyResult.Should().BeTrue();

            // Close
            var closeResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderClose,
                TestDataFactory.OrderClosePayload()));
            closeResult.Should().BeTrue();
            _orderExecutionService.Received(1).SubmitOrder(Arg.Is<BrokerOrder>(o => o.Name == "Close_test-1"));
        }

        [Fact]
        public void OpenModifyCloseFlow_Simulation_SendsFillsWithoutBroker()
        {
            _tradingMode.IsSimulation.Returns(true);

            var openResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(entryPrice: 20000, stopLoss: 19980, takeProfit: 20040, contracts: 2)));
            openResult.Should().BeTrue();

            var modifyResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19990, takeProfit: 20030)));
            modifyResult.Should().BeTrue();

            var closeResult = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderClose,
                TestDataFactory.OrderClosePayload()));
            closeResult.Should().BeTrue();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateEntryOrder(null, null, default, 0, null);
            _network.Received(1).SendEntryFill("test-1", 20000, 19980, 20040, account: (string)null);
            _network.Received(1).SendExitFill("test-1", 0, "CLOSE", account: (string)null);
        }

        [Fact]
        public void RefreshFlow_SendsHistoryBatches()
        {
            var clock = Substitute.For<IConnectorClock>();
            var barHistoryService = Substitute.For<IBarHistoryService>();
            var config = TestDataFactory.Config(batchSize: 2);
            var instrument = TestDataFactory.Instrument();
            var now = new System.DateTime(2025, 1, 10, 12, 0, 0, System.DateTimeKind.Utc);
            clock.UtcNow.Returns(now);
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);

            var bars = Enumerable.Range(0, 5).Select(i => TestDataFactory.Bar(time: now.AddMinutes(-5 + i))).ToList();
            barHistoryService.RequestHistoryAsync(instrument, Arg.Any<System.DateTime>(), Arg.Any<System.DateTime>())
                .Returns(Task.FromResult<IReadOnlyList<Bar>>(bars));

            _dispatcher.Register(new RefreshRequestHandler(_network, _logger, _instrumentProvider, barHistoryService, clock, config));

            var result = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.RefreshRequest,
                TestDataFactory.RefreshPayload("MNQ 09-25", 3)));
            result.Should().BeTrue();

            System.Threading.Thread.Sleep(200);
            _network.Received(1).SendRefreshStart();
            _network.Received(3).SendHistoryBatch("MNQ", Arg.Any<List<JObject>>(), Arg.Any<int>());
            _network.Received(1).SendHistoryEnd();
        }
    }
}
