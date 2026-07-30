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
            _network.Received(1).SendRefreshStart();
            _network.Received(3).SendHistoryBatch("MNQ", Arg.Any<List<JObject>>(), Arg.Any<int>());
            _network.Received(1).SendHistoryEnd();
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

        [Fact]
        public void OpenPartialFillDeferredAmend_WhenOrdersWorking_AmendApplied_StopFills_CleanClose()
        {
            // Full dispatcher pipeline replay of the 2026-07-30 incident: deferred
            // amendment applied on Working transitions, then the amended stop fills and
            // the trade closes cleanly — no orphan, no guard, no errors.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("t1");

            var entryOrder = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_t1", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "t1").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t1").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t1").Returns(targetInit);

            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not modifiable"); });

            h.Open("t1", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);
            h.Logger.Warnings.Should().Contain(m => m.Contains("BRACKET AMENDMENT PENDING") && m.Contains("stop state=Initialized"));

            modifiable = true;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));
            h.Logger.Successes.Should().Contain(m => m.Contains("BRACKET UPDATED") && m.Contains("t1"));

            // Live lookups for cleanup: the stop is now filled (terminal), the target still live.
            var stopFilled = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25022, stopPrice: 25022, orderType: OrderType.StopMarket);
            var targetLive = targetInit.WithState(OrderState.Working);
            h.Execution.FindOrderByName(h.Account, "Stop_t1").Returns(stopFilled);
            h.Execution.FindOrderByName(h.Account, "Target_t1").Returns(targetLive);

            h.Service.OnExecutionUpdate(stopFilled, 25022, 6);

            // Tracker fully cleaned; the surviving target leg is cancelled as cleanup.
            h.Tracker.GetActiveTradeIds().Should().BeEmpty();
            h.Execution.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_t1"));

            // Python saw both entry fills and exactly one exit fill; no errors, no orphan close.
            h.Network.Received(2).SendEntryFill("t1", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
            h.Network.Received(1).SendExitFill("t1", 25022, "SL", Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>());
            h.Network.DidNotReceiveWithAnyArgs().SendError(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
            h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
        }

        [Fact]
        public void SafetyGuard_DoesNotFire_DuringDeferredAmendmentWindow()
        {
            // While an amendment is pending and the tracked stop exists (still
            // Initialized), the safety guard must never act — the entry-fill grace
            // period covers the transition window.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("t2");

            var entryOrder = TestDataFactory.Order(name: "Entry_t2", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_t2", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_t2", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_t2", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_t2", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "t2").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t2").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t2").Returns(targetInit);
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => throw new InvalidOperationException("not modifiable"));
            h.Execution.GetAccountPositions(h.Account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(h.Account.Name, h.Instrument, 6, "short", 25002)
            });

            h.Open("t2", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);   // deferred; tracked legs still Initialized
            h.Service.OnOrderUpdate(entryFull);                 // entry tracked with Filled=6
            h.Logger.Warnings.Should().Contain(m => m.Contains("BRACKET AMENDMENT PENDING"));

            h.Service.RunSafetyCheckOnce();

            h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            h.Network.DidNotReceiveWithAnyArgs().SendError(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
            h.Tracker.TryGetEntry("t2", out _).Should().BeTrue();
        }

        [Fact]
        public void OrderClose_WithStaleInitializedSnapshot_CancelsLiveOrder()
        {
            // The tracked stop snapshot is stale (Initialized) but the live order is
            // Working: bracket cleanup must consult the live state and cancel it
            // (pre-2026-07-30 the stale snapshot made it skip the cancel entirely).

            var h = new ConnectorHarness();
            h.MockTradeIdNames("t3");
            h.RegisterCloseHandler();

            var entryOrder = TestDataFactory.Order(name: "Entry_t3", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_t3", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var stopInit = TestDataFactory.Order(name: "Stop_t3", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_t3", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "t3").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t3").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t3").Returns(targetInit);

            h.Open("t3", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);

            // ORDER_CLOSE pipeline: entry filled at the broker, bracket legs live+Working.
            var entryFilled = TestDataFactory.Order(name: "Entry_t3", side: OrderSide.SellShort, state: OrderState.Filled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var stopLive = stopInit.WithState(OrderState.Working);
            var targetLive = targetInit.WithState(OrderState.Working);
            var closeOrder = TestDataFactory.Order(name: "Close_t3", side: OrderSide.BuyToCover, state: OrderState.Working, quantity: 4, instrument: h.Instrument);
            var closeFilled = TestDataFactory.Order(name: "Close_t3", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 4, quantity: 4, instrument: h.Instrument, avgFill: 25010);

            h.Execution.FindOrderByName(h.Account, "Entry_t3").Returns(entryFilled);
            h.Execution.FindOrderByName(h.Account, "Stop_t3").Returns(stopLive);
            h.Execution.FindOrderByName(h.Account, "Target_t3").Returns(targetLive);
            h.Execution.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), h.Account, OrderSide.BuyToCover, 4, "t3").Returns(closeOrder);

            h.Dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderClose,
                TestDataFactory.OrderClosePayload(tradeId: "t3", account: "Sim101"))).Should().BeTrue();
            h.Execution.Received(1).SubmitOrder(Arg.Is<BrokerOrder>(o => o.Name == "Close_t3"));

            // The close fills; bracket cleanup cancels the live legs even though the
            // TRACKED snapshots are still Initialized.
            h.Service.OnExecutionUpdate(closeFilled, 25010, 4);

            h.Logger.Infos.Should().Contain(m => m.Contains("Cancelling stop for t3") && m.Contains("tracked=Initialized") && m.Contains("live=Working"));
            // Once from the ORDER_CLOSE handler, once from the post-close bracket cleanup.
            h.Execution.Received(2).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Stop_t3"));
            h.Execution.Received(2).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_t3"));
        }

        [Fact]
        public void OrderClose_AfterDeferredAmendment_ClosesAtAmendedQuantity()
        {
            // Full pipeline: deferred amendment applied, then ORDER_CLOSE — the close
            // order is for the final cumulative quantity and both bracket legs cancel.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("t4");
            h.RegisterCloseHandler();

            var entryOrder = TestDataFactory.Order(name: "Entry_t4", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_t4", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_t4", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_t4", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_t4", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "t4").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t4").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t4").Returns(targetInit);

            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not modifiable"); });

            h.Open("t4", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);   // deferred
            modifiable = true;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));

            // The tracked bracket now carries the amended cumulative quantity.
            h.Tracker.TryGetStopLoss("t4", out var amendedStop).Should().BeTrue();
            amendedStop.Quantity.Should().Be(6);

            var entryFilled = TestDataFactory.Order(name: "Entry_t4", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopLive = stopInit.WithState(OrderState.Working);
            var targetLive = targetInit.WithState(OrderState.Working);
            var closeOrder = TestDataFactory.Order(name: "Close_t4", side: OrderSide.BuyToCover, state: OrderState.Working, quantity: 6, instrument: h.Instrument);
            h.Execution.FindOrderByName(h.Account, "Entry_t4").Returns(entryFilled);
            h.Execution.FindOrderByName(h.Account, "Stop_t4").Returns(stopLive);
            h.Execution.FindOrderByName(h.Account, "Target_t4").Returns(targetLive);
            h.Execution.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), h.Account, OrderSide.BuyToCover, 6, "t4").Returns(closeOrder);

            h.Dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderClose,
                TestDataFactory.OrderClosePayload(tradeId: "t4", account: "Sim101"))).Should().BeTrue();

            h.Execution.Received(1).CreateMarketCloseOrder(h.Instrument, h.Account, OrderSide.BuyToCover, 6, "t4");
            h.Execution.Received(1).SubmitOrder(Arg.Is<BrokerOrder>(o => o.Name == "Close_t4"));
            h.Execution.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Stop_t4"));
            h.Execution.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_t4"));
            h.Network.DidNotReceiveWithAnyArgs().SendError(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
        }

        [Fact]
        public void DuplicateLiveBracket_Detected_ReportsCriticalError()
        {
            // Tripwire: two non-terminal orders sharing a bracket name must be impossible
            // after the 2026-07-30 fix — if it ever happens, report CRITICAL immediately.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("t5");

            var entryOrder = TestDataFactory.Order(name: "Entry_t5", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_t5", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var stopInit = TestDataFactory.Order(name: "Stop_t5", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_t5", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "t5").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t5").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "t5").Returns(targetInit);

            h.Open("t5", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);

            // Live state: the stop filled; TWO non-terminal targets share the same name.
            var stopFilled = TestDataFactory.Order(name: "Stop_t5", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 4, quantity: 4, instrument: h.Instrument, avgFill: 25020, stopPrice: 25020, orderType: OrderType.StopMarket);
            var dupTarget1 = targetInit.WithState(OrderState.Working);
            var dupTarget2 = TestDataFactory.Order(name: "Target_t5", side: OrderSide.BuyToCover, state: OrderState.Accepted, quantity: 6, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);
            h.Execution.FindOrderByName(h.Account, "Stop_t5").Returns(stopFilled);
            h.Execution.FindOrderByName(h.Account, "Target_t5").Returns(dupTarget1);
            h.Execution.GetAllOrders(h.Account).Returns(new List<BrokerOrder> { stopFilled, dupTarget1, dupTarget2 });

            // The stop fill triggers bracket cleanup, which spots the duplicate.
            h.Service.OnExecutionUpdate(stopFilled, 25020, 4);

            h.Logger.Errors.Should().Contain(e => e.Message.Contains("CRITICAL") && e.Message.Contains("duplicate live target orders") && e.Message.Contains("t5"));
            h.Network.Received(1).SendError("ninjatrader", "duplicate_bracket_detected", Arg.Is<string>(s => s.Contains("t5")));
            // The live target is still cancelled as cleanup.
            h.Execution.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_t5"));
        }

        [Fact]
        public void EndToEnd_2026_07_30_Replay_GuardNeverFires()
        {
            // Connect()-loop replay of the 2026-07-30 incident: the safety loop runs at
            // 50ms throughout the deferred-amendment window and must never fire; exactly
            // one bracket generation exists across the whole run.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("incident");
            h.Service.SafetyGuardEnabled = true;
            h.Service.SafetyCheckIntervalMs = 50;

            var entryOrder = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_incident", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_incident", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);
            var stopLive = stopInit.WithState(OrderState.Working);
            var targetLive = targetInit.WithState(OrderState.Working);
            var stopFilled = TestDataFactory.Order(name: "Stop_incident", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25022, stopPrice: 25022, orderType: OrderType.StopMarket);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "incident").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident").Returns(targetInit);
            h.Execution.GetAccountPositions(h.Account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(h.Account.Name, h.Instrument, 6, "short", 25001)
            });
            h.Execution.GetWorkingOrders(h.Account).Returns(new List<BrokerOrder> { stopLive });
            h.Execution.FindOrderByName(h.Account, "Stop_incident").Returns(stopFilled);
            h.Execution.FindOrderByName(h.Account, "Target_incident").Returns(targetLive);

            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not modifiable"); });

            h.Service.Connect();
            try
            {
                h.Open("incident", "short", 6).Should().BeTrue();
                h.Service.OnOrderUpdate(entryOrder);
                h.Service.OnExecutionUpdate(entryPartial, 25000, 4);   // bracket #1, still Initialized
                h.Service.OnOrderUpdate(entryPartial);
                h.Service.OnExecutionUpdate(entryFull, 25002, 2);      // amend rejected → deferred
                h.Service.OnOrderUpdate(entryFull);
                modifiable = true;
                h.Service.OnOrderUpdate(stopLive);                     // legs reach Working
                h.Service.OnOrderUpdate(targetLive);

                // The amended qty=6 stop sweeps and fills; the position goes flat at the broker.
                h.Execution.GetAccountPositions(h.Account).Returns(new List<BrokerPosition>());
                h.Service.OnExecutionUpdate(stopFilled, 25022, 6);

                Thread.Sleep(250); // let the safety loop run several cycles

                // Exactly one bracket generation; no cancel before terminal state.
                h.Execution.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident");
                h.Execution.Received(1).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident");
                h.Execution.Received(1).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());

                // Only the surviving target leg was cancelled — as cleanup after the stop fill.
                h.Execution.ReceivedWithAnyArgs(1).CancelOrder(Arg.Any<BrokerOrder>());
                h.Execution.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_incident"));
                h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
                h.Network.DidNotReceive().SendError("ninjatrader", "missing_stop_loss_guard", Arg.Any<string>());
                h.Tracker.GetActiveTradeIds().Should().BeEmpty();
            }
            finally
            {
                h.Service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void EndToEnd_OrphanSweep_StillFlattens_GenuineOrphan()
        {
            // Regression guard: an account position with no tracked entry and no working
            // stop is still flattened by the sweep — the safety net stays intact as the
            // last resort.

            var h = new ConnectorHarness();
            h.Service.SafetyGuardEnabled = true;
            h.Service.SafetyCheckIntervalMs = 50;

            var orphanClose = TestDataFactory.Order(name: "Close_orphan", side: OrderSide.Sell, state: OrderState.Working, quantity: 4, instrument: h.Instrument);
            h.Execution.GetAccountPositions(h.Account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(h.Account.Name, h.Instrument, 4, "long", 25000)
            });
            h.Execution.GetWorkingOrders(h.Account).Returns(new List<BrokerOrder>());
            h.Execution.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), h.Account, OrderSide.Sell, 4, Arg.Any<string>()).Returns(orphanClose);

            var flattened = new ManualResetEventSlim(false);
            h.Execution.When(x => x.SubmitOrder(Arg.Is<BrokerOrder>(o => o.Name == "Close_orphan"))).Do(_ => flattened.Set());

            h.Service.Connect();
            try
            {
                flattened.Wait(TimeSpan.FromSeconds(2)).Should().BeTrue("orphan sweep should flatten a genuine orphan position");
                h.Network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Is<string>(s => s.Contains("Orphan")));
            }
            finally
            {
                h.Service.Disconnect("cleanup");
            }
        }
    }
}
