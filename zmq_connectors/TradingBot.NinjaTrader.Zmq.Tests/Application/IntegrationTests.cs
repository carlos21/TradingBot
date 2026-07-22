using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
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
            _network.Received(1).SendEntryFill("test-1", 20000, 19980, 20040, account: (string)null, quantity: 2);
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
            _network.Received(1).SendRefreshStart("MNQ");
            _network.Received(3).SendHistoryBatch("MNQ", Arg.Any<List<JObject>>(), Arg.Any<int>());
            _network.Received(1).SendHistoryEnd("MNQ");
        }

        [Fact]
        public void SafetyGuard_EndToEnd_Flattens_WhenBracketDisappears()
        {
            // Full connector wiring: open a trade, partial-fill it, attach a bracket,
            // then simulate the stop-loss disappearing. The background safety loop must
            // flatten the position within its check interval.
            var network = Substitute.For<IZmqNetwork>();
            var logger = new TestLogger();
            var orderTracker = new InMemoryOrderTracker();
            var dispatcher = new CommandDispatcher(logger);
            var accountProvider = Substitute.For<IAccountProvider>();
            var instrumentProvider = Substitute.For<IInstrumentProvider>();
            var orderExecutionService = Substitute.For<IOrderExecutionService>();
            var tradeIdExtractor = Substitute.For<ITradeIdExtractor>();
            var clock = Substitute.For<IConnectorClock>();
            var streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            var barHistoryService = Substitute.For<IBarHistoryService>();
            var pnlCalculator = Substitute.For<IPnLCalculator>();
            var config = TestDataFactory.Config();

            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 2, instrument: instrument, avgFill: 20000);
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);
            var closeOrder = TestDataFactory.Order(name: "Close_test-1", side: OrderSide.Sell, state: OrderState.Working);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1").Returns(targetOrder);
            orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "test-1").Returns(closeOrder);
            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { stopOrder });

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);
            service.SafetyGuardEnabled = true;
            service.SafetyCheckIntervalMs = 50;

            // Open the trade through the real dispatcher/handler pipeline.
            var openResult = dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(riskUsd: 100, riskPoints: 10)));
            openResult.Should().BeTrue();

            // Simulate a partial entry fill. The handler should attach a bracket.
            tradeIdExtractor.ExtractTradeId("Entry_test-1").Returns("test-1");
            tradeIdExtractor.IsEntryOrder("Entry_test-1").Returns(true);
            service.OnExecutionUpdate(entryOrder, 20000, 2);
            orderExecutionService.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1");

            // Now the stop-loss disappears (cancelled externally or never attached).
            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());

            // Advance past the entry-fill grace period and simulate the broker still holding the position.
            clock.UtcNow.Returns(DateTime.UtcNow.AddSeconds(3));
            orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });

            var flattened = new ManualResetEventSlim(false);
            orderExecutionService.When(x => x.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "test-1"))
                .Do(x => flattened.Set());

            service.Connect();
            try
            {
                flattened.Wait(TimeSpan.FromMilliseconds(500)).Should().BeTrue("safety guard should flatten after bracket disappears");
                orderExecutionService.Received(1).SubmitOrder(closeOrder);
                network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Is<string>(s => s.Contains("test-1")));
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void ModifyStopLoss_Live_CallsModifyOrder_NotCancel()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19980);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            var result = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19990)));

            result.Should().BeTrue();
            _orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19990) < 0.01),
                Arg.Is<double?>(x => x == null));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
            _orderTracker.TryGetPendingModify("test-1:sl", out _).Should().BeFalse();
        }

        [Fact]
        public void ModifyStopLossAndTakeProfit_Live_CallsModifyOrderForBoth()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19980);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);

            var result = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19990, takeProfit: 20050)));

            result.Should().BeTrue();
            _orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19990) < 0.01),
                Arg.Is<double?>(x => x == null));
            _orderExecutionService.Received(1).ModifyOrder(
                targetOrder,
                Arg.Is<double?>(x => x == null),
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 20050) < 0.01));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
        }

        [Fact]
        public void ModifyStopLoss_Live_ReturnsFalse_WhenOrderNotWorking()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Filled, stopPrice: 19980);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            var result = _dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19990)));

            result.Should().BeFalse();
            _orderExecutionService.DidNotReceiveWithAnyArgs().ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void SafetyGuard_DoesNotFlatten_AfterSuccessfulModify()
        {
            var network = Substitute.For<IZmqNetwork>();
            var logger = new TestLogger();
            var orderTracker = new InMemoryOrderTracker();
            var dispatcher = new CommandDispatcher(logger);
            var accountProvider = Substitute.For<IAccountProvider>();
            var instrumentProvider = Substitute.For<IInstrumentProvider>();
            var orderExecutionService = Substitute.For<IOrderExecutionService>();
            var tradeIdExtractor = Substitute.For<ITradeIdExtractor>();
            var clock = Substitute.For<IConnectorClock>();
            var streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            var barHistoryService = Substitute.For<IBarHistoryService>();
            var pnlCalculator = Substitute.For<IPnLCalculator>();
            var config = TestDataFactory.Config();

            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new OrderModifyHandler(network, logger, orderTracker, tradeIdExtractor, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 2, instrument: instrument, avgFill: 20000);
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1").Returns(targetOrder);
            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { stopOrder, targetOrder });

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);
            service.SafetyGuardEnabled = true;

            // Open and attach bracket.
            tradeIdExtractor.ExtractTradeId("Entry_test-1").Returns("test-1");
            tradeIdExtractor.IsEntryOrder("Entry_test-1").Returns(true);
            tradeIdExtractor.IsStopOrder("Stop_test-1").Returns(true);
            tradeIdExtractor.IsTargetOrder("Target_test-1").Returns(true);
            dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(riskUsd: 100, riskPoints: 10))).Should().BeTrue();
            service.OnExecutionUpdate(entryOrder, 20000, 2);
            orderExecutionService.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "test-1");

            // Move the stop-loss in place.
            tradeIdExtractor.ExtractTradeId("Stop_test-1").Returns("test-1");
            dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderModify,
                TestDataFactory.OrderModifyPayload(stopLoss: 19995))).Should().BeTrue();
            orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19995) < 0.01),
                Arg.Is<double?>(x => x == null));

            // Safety check must NOT flatten while the stop is still working.
            service.RunSafetyCheckOnce();
            orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            network.DidNotReceiveWithAnyArgs().SendError(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
        }
    }
}
