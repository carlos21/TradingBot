using System;
using System.Collections.Generic;
using System.Threading;
using FluentAssertions;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    /// <summary>
    /// Deterministic replay of the 2026-07-07 incident: an oversized order is
    /// partially filled, the protective bracket fails to attach, and the
    /// connector must flatten immediately instead of leaving contracts exposed.
    /// </summary>
    public class IncidentSimulationTests
    {
        [Fact]
        public void PartialFill_WithFailedBracketAttachment_IsFlattenedImmediately()
        {
            // This reproduces the dangerous state from the incident:
            // - Account ~$50k, 1.6% risk, 20-point stop on MNQ -> 20 contracts.
            // - Market order is partially filled (7 contracts in the first chunk).
            // - The stop-loss order cannot be created (simulates missing bracket).
            // Result: the filled portion is flattened instantly, before any
            // further partial fills or adverse move can occur.

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

            var account = TestDataFactory.Account(cashValue: 50000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            // The live market order is created for 20 contracts.
            var entryOrder = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 7, quantity: 20, instrument: instrument, avgFill: 30157.25);
            var closeOrder = TestDataFactory.Order(name: "Close_incident", side: OrderSide.Sell, state: OrderState.Working);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, 20, "incident").Returns(entryOrder);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident").Returns((BrokerOrder)null);
            orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 7, "incident").Returns(closeOrder);

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);

            // Open the trade through the real ORDER_OPEN handler.
            var openResult = dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(tradeId: "incident", riskPct: 1.6, riskPoints: 20, account: "Sim101")));
            openResult.Should().BeTrue();

            orderExecutionService.Received(1).CreateEntryOrder(instrument, account, OrderSide.Buy, 20, "incident");
            orderExecutionService.Received(1).SubmitOrder(entryOrder);

            // Simulate the first partial fill. Because CreateStopLossOrder returns null,
            // the bracket cannot be attached and the connector must flatten the 7
            // contracts that are already in the market.
            tradeIdExtractor.ExtractTradeId("Entry_incident").Returns("incident");
            tradeIdExtractor.IsEntryOrder("Entry_incident").Returns(true);
            service.OnExecutionUpdate(entryOrder, 30157.25, 7);

            orderExecutionService.Received(1).CreateMarketCloseOrder(Arg.Is<BrokerInstrument>(i => i.Name == instrument.Name), account, OrderSide.Sell, 7, "incident");
            orderExecutionService.Received(1).SubmitOrder(closeOrder);
            network.Received(1).SendError("ninjatrader", "bracket_creation_failed", Arg.Is<string>(s => s.Contains("incident")));
            network.Received(1).SendTradeLog("incident", "NT:FLATTEN", Arg.Is<string>(s => s.Contains("7")));
        }

        [Fact]
        public void PartialFill_WithoutWorkingStop_IsFlattenedBySafetyGuard()
        {
            // Slightly different angle: the bracket *appears* to attach, but the
            // stop order is not actually working at the broker. The background
            // safety loop must flatten the position within its check interval.

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

            var account = TestDataFactory.Account(cashValue: 50000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryOrder = TestDataFactory.Order(name: "Entry_guard", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 7, quantity: 20, instrument: instrument, avgFill: 30157.25);
            var stopOrder = TestDataFactory.Order(name: "Stop_guard", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 30137.25);
            var targetOrder = TestDataFactory.Order(name: "Target_guard", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 30257.25);
            var closeOrder = TestDataFactory.Order(name: "Close_guard", side: OrderSide.Sell, state: OrderState.Working);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, 20, "guard").Returns(entryOrder);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "guard").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "guard").Returns(targetOrder);
            orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "guard").Returns(closeOrder);
            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { entryOrder });

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);
            service.SafetyGuardEnabled = true;
            service.SafetyCheckIntervalMs = 50;

            var flattened = new ManualResetEventSlim(false);
            orderExecutionService.When(x => x.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "guard"))
                .Do(x => flattened.Set());

            tradeIdExtractor.ExtractTradeId("Entry_guard").Returns("guard");
            tradeIdExtractor.IsEntryOrder("Entry_guard").Returns(true);

            dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(tradeId: "guard", riskPct: 1.6, riskPoints: 20, account: "Sim101")));
            service.OnExecutionUpdate(entryOrder, 30157.25, 7);

            // Advance past the entry-fill grace period and simulate the broker still holding the position.
            clock.UtcNow.Returns(DateTime.UtcNow.AddSeconds(3));
            orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 7, "long", 30157.25)
            });

            // The tracker thinks a stop exists, but the broker does not report it working.
            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { entryOrder });

            service.Connect();
            try
            {
                flattened.Wait(TimeSpan.FromMilliseconds(500)).Should().BeTrue("safety guard should flatten after stop disappears");
                orderExecutionService.Received(1).SubmitOrder(closeOrder);
                network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Is<string>(s => s.Contains("guard")));
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }
        [Fact]
        public void PartialFill_ThenFullFill_ModifiesExistingBracket_InsteadOfCreatingSecond()
        {
            // Replay of the 2026-07-29 incident: 3 MNQ contracts, partial fill of 1,
            // then the full fill 214ms later. The second fill must MODIFY the existing
            // bracket in place — never create a second bracket with the same names/OCO.

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

            // balance=9000, riskPct=2, riskPoints=30 -> risk $180 / $60 per contract = qty 3.
            var account = TestDataFactory.Account(cashValue: 9000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryPartial = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1, quantity: 3, instrument: instrument, avgFill: 21000);
            var entryFull = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.Filled, filled: 3, quantity: 3, instrument: instrument, avgFill: 21002);
            var stopOrder = TestDataFactory.Order(name: "Stop_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, stopPrice: 20970, orderType: OrderType.StopMarket);
            var targetOrder = TestDataFactory.Order(name: "Target_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, limitPrice: 21060, orderType: OrderType.Limit);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, 3, "replay").Returns(entryPartial);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(targetOrder);

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);

            tradeIdExtractor.ExtractTradeId("Entry_replay").Returns("replay");
            tradeIdExtractor.IsEntryOrder("Entry_replay").Returns(true);
            tradeIdExtractor.ExtractTradeId("Stop_replay").Returns("replay");
            tradeIdExtractor.IsStopOrder("Stop_replay").Returns(true);

            var openResult = dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(tradeId: "replay", riskPct: 2, riskPoints: 30, account: "Sim101")));
            openResult.Should().BeTrue();

            // Partial fill: 1 of 3 contracts — bracket #1 is created for the filled qty.
            // SL = 21000 - 30 = 20970, TP = 21000 + 30*2 = 21060.
            service.OnExecutionUpdate(entryPartial, 21000, 1);

            orderExecutionService.Received(1).CreateStopLossOrder(instrument, account, OrderSide.Sell, 1, 20970, "replay");
            orderExecutionService.Received(1).CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 1, 21060, "replay");
            orderExecutionService.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(l => l.Count == 2));

            // Full fill: 3 of 3 contracts at an updated average price.
            // New SL = 21002 - 30 = 20972, new TP = 21002 + 60 = 21062.
            service.OnExecutionUpdate(entryFull, 21002, 2);

            // No second bracket may be created, submitted, or cancelled.
            orderExecutionService.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay");
            orderExecutionService.Received(1).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay");
            orderExecutionService.Received(1).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());
            orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());

            // The existing bracket legs are amended in place: new prices and qty=3.
            orderExecutionService.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_replay"),
                Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 20972) < 0.01),
                Arg.Is<double?>(l => !l.HasValue),
                Arg.Is<int?>(q => q.HasValue && q.Value == 3));
            orderExecutionService.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Target_replay"),
                Arg.Is<double?>(s => !s.HasValue),
                Arg.Is<double?>(l => l.HasValue && Math.Abs(l.Value - 21062) < 0.01),
                Arg.Is<int?>(q => q.HasValue && q.Value == 3));

            // The tracked bracket snapshot reflects the amended qty/prices.
            orderTracker.TryGetStopLoss("replay", out var trackedStop).Should().BeTrue();
            trackedStop.Quantity.Should().Be(3);
            trackedStop.StopPrice.Should().BeApproximately(20972, 0.01);
            orderTracker.TryGetTakeProfit("replay", out var trackedTarget).Should().BeTrue();
            trackedTarget.Quantity.Should().Be(3);
            trackedTarget.LimitPrice.Should().BeApproximately(21062, 0.01);

            // Every fill still sends an entry-fill notification; the second carries cumulative qty 3.
            network.Received(2).SendEntryFill("replay", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
            network.Received(1).SendEntryFill("replay", 21002,
                Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 20972) < 0.01),
                Arg.Is<double?>(t => t.HasValue && Math.Abs(t.Value - 21062) < 0.01),
                Arg.Any<double?>(), Arg.Any<string>(),
                Arg.Is<double?>(q => q.HasValue && Math.Abs(q.Value - 3) < 0.01), Arg.Any<double?>());
        }

        [Fact]
        public void FullFill_WhenBracketModifyFails_FallsBackToRecreate()
        {
            // If the broker rejects the in-place amend AND the tracked bracket legs are
            // already terminal (a genuinely dead bracket), the connector must recreate
            // immediately — and must NOT flatten when recreate succeeds.
            // (Since the 2026-07-30 deferred-amendment fix, a modify failure against a
            // LIVE bracket defers instead of recreating; this test keeps covering the
            // terminal-leg recreate path.)

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

            var account = TestDataFactory.Account(cashValue: 9000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryPartial = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1, quantity: 3, instrument: instrument, avgFill: 21000);
            var entryFull = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.Filled, filled: 3, quantity: 3, instrument: instrument, avgFill: 21002);
            var stopOrder = TestDataFactory.Order(name: "Stop_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, stopPrice: 20970, orderType: OrderType.StopMarket);
            var targetOrder = TestDataFactory.Order(name: "Target_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, limitPrice: 21060, orderType: OrderType.Limit);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, 3, "replay").Returns(entryPartial);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(targetOrder);
            orderExecutionService
                .When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => throw new InvalidOperationException("NT rejected amend"));

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);

            tradeIdExtractor.ExtractTradeId("Entry_replay").Returns("replay");
            tradeIdExtractor.IsEntryOrder("Entry_replay").Returns(true);

            dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(tradeId: "replay", riskPct: 2, riskPoints: 30, account: "Sim101")));
            service.OnExecutionUpdate(entryPartial, 21000, 1);

            // Put the tracked bracket legs in a terminal state: a dead bracket is
            // recreated immediately (a live one now defers the amendment instead).
            orderTracker.TrackStopLoss("replay", stopOrder.WithState(OrderState.Cancelled));
            orderTracker.TrackTakeProfit("replay", targetOrder.WithState(OrderState.Cancelled));

            service.OnExecutionUpdate(entryFull, 21002, 2);

            // The dead legs are not cancelled (already terminal); a fresh bracket is created+submitted.
            orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
            orderExecutionService.Received(2).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay");
            orderExecutionService.Received(2).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay");
            orderExecutionService.Received(2).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());

            // Recreate succeeded, so the position must NOT be flattened and no error escapes.
            orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            network.DidNotReceive().SendError("ninjatrader", "bracket_creation_failed", Arg.Any<string>());
            network.Received(2).SendEntryFill("replay", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void ModifiedBracket_StopFill_ClosesCleanly_WithoutOrphan()
        {
            // After the partial-then-full modify sequence, the amended qty=3 stop fills.
            // The trade must close cleanly: tracker cleaned up, remaining leg cancelled,
            // and no orphan market close or safety-guard error.

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

            var account = TestDataFactory.Account(cashValue: 9000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var tradingMode = Substitute.For<ITradingMode>();
            tradingMode.IsSimulation.Returns(false);

            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));

            var entryPartial = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1, quantity: 3, instrument: instrument, avgFill: 21000);
            var entryFull = TestDataFactory.Order(name: "Entry_replay", side: OrderSide.Buy, state: OrderState.Filled, filled: 3, quantity: 3, instrument: instrument, avgFill: 21002);
            var stopOrder = TestDataFactory.Order(name: "Stop_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, stopPrice: 20970, orderType: OrderType.StopMarket);
            var targetOrder = TestDataFactory.Order(name: "Target_replay", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, limitPrice: 21060, orderType: OrderType.Limit);

            orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, 3, "replay").Returns(entryPartial);
            orderExecutionService.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(stopOrder);
            orderExecutionService.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "replay").Returns(targetOrder);

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);

            tradeIdExtractor.ExtractTradeId("Entry_replay").Returns("replay");
            tradeIdExtractor.IsEntryOrder("Entry_replay").Returns(true);
            tradeIdExtractor.ExtractTradeId("Stop_replay").Returns("replay");
            tradeIdExtractor.IsStopOrder("Stop_replay").Returns(true);

            dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(tradeId: "replay", riskPct: 2, riskPoints: 30, account: "Sim101")));
            service.OnExecutionUpdate(entryPartial, 21000, 1);
            service.OnExecutionUpdate(entryFull, 21002, 2);

            // The amended qty=3 stop fills completely.
            var stopFill = TestDataFactory.Order(name: "Stop_replay", side: OrderSide.Sell, state: OrderState.Filled, filled: 3, quantity: 3, instrument: instrument, avgFill: 20972, stopPrice: 20972, orderType: OrderType.StopMarket);
            service.OnExecutionUpdate(stopFill, 20972, 3);

            // Trade is fully removed from tracking.
            orderTracker.TryGetEntry("replay", out _).Should().BeFalse();
            orderTracker.TryGetStopLoss("replay", out _).Should().BeFalse();
            orderTracker.TryGetTakeProfit("replay", out _).Should().BeFalse();

            // The remaining bracket leg (target) is cancelled as cleanup.
            orderExecutionService.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_replay"));

            // No orphan close is submitted and the safety guard never fires.
            orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            network.DidNotReceive().SendError("ninjatrader", "missing_stop_loss_guard", Arg.Any<string>());
            network.Received(1).SendExitFill("replay", 20972, "SL", Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void SafetyGuard_StopQuantityBelowPosition_IsTreatedAsUnprotected()
        {
            // Quantity-aware guard: a WORKING stop exists, but only for 1 contract while
            // the broker holds a 3-lot. The position is under-protected and must be flattened.

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

            var account = TestDataFactory.Account(cashValue: 50000);
            var instrument = TestDataFactory.Instrument();
            accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            accountProvider.GetAccount("Sim101").Returns(account);
            clock.UtcNow.Returns(DateTime.UtcNow);

            var service = new ConnectorService(
                config, network, logger, dispatcher, orderTracker, streamingCoordinator,
                accountProvider, orderExecutionService, instrumentProvider, barHistoryService,
                pnlCalculator, clock, tradeIdExtractor);
            service.SafetyGuardEnabled = true;

            // Broker reports a working stop for only 1 contract (in the closing direction)
            // against an orphan long position of 3 contracts.
            var undersizedStop = TestDataFactory.Order(name: "Stop_leftover", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: instrument, stopPrice: 20970, orderType: OrderType.StopMarket);
            var closeOrder = TestDataFactory.Order(name: "Close_orphan", side: OrderSide.Sell, state: OrderState.Working, quantity: 3, instrument: instrument);

            orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { undersizedStop });
            orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 3, "long", 21000)
            });
            orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 3, Arg.Any<string>()).Returns(closeOrder);

            service.RunSafetyCheckOnce();

            orderExecutionService.Received(1).CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 3, Arg.Any<string>());
            orderExecutionService.Received(1).SubmitOrder(closeOrder);
            network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Any<string>());
        }

        [Fact]
        public void PartialFill_ThenFullFill_WhenBracketNotYetModifiable_DefersAmendment_NoDuplicateBracket()
        {
            // Replay of the 2026-07-30 incident (trade 1f0fdffc): x6 short, partial fill
            // of 4, bracket #1 created but still Initialized when the remaining 2 fill
            // ~220ms later. The amend must be DEFERRED — never cancel+recreate against
            // live orders — and applied once the legs reach a modifiable state.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("incident");

            var entryOrder = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_incident", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_incident", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_incident", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "incident").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident").Returns(targetInit);

            // NT rejects the amend while the bracket submit is still in flight.
            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not found or not in a modifiable state"); });

            h.Open("incident", "short", 6).Should().BeTrue();
            h.Execution.Received(1).CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "incident");

            // First chunk: 4 of 6 short — bracket #1 is created for the filled qty.
            // SL = 25000 + 20 = 25020, TP = 25000 - 40 = 24960.
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Execution.Received(1).CreateStopLossOrder(h.Instrument, h.Account, OrderSide.BuyToCover, 4, 25020, "incident");
            h.Execution.Received(1).CreateTakeProfitOrder(h.Instrument, h.Account, OrderSide.BuyToCover, 4, 24960, "incident");
            h.Execution.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(l => l.Count == 2));

            // The remaining 2 fill; bracket #1 is still Initialized, so NT rejects the
            // amend. Desired: SL = 25002 + 20 = 25022, TP = 25002 - 40 = 24962, qty = 6.
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);

            // NO second bracket generation, NO cancel, NO flatten — amendment deferred.
            h.Execution.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident");
            h.Execution.Received(1).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "incident");
            h.Execution.Received(1).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());
            h.Execution.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
            h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            h.Logger.Warnings.Should().Contain(m => m.Contains("BRACKET AMENDMENT PENDING") && m.Contains("incident") && m.Contains("qty=6"));
            h.Network.Received(1).SendTradeLog("incident", "NT:WARNING", Arg.Is<string>(s => s.Contains("Bracket amendment pending")));

            // NT then reports the legs Working — the deferred amendment applies.
            modifiable = true;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));

            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_incident" && o.OrderState == OrderState.Working),
                Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 25022) < 0.01),
                Arg.Is<double?>(l => !l.HasValue),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Target_incident" && o.OrderState == OrderState.Working),
                Arg.Is<double?>(s => !s.HasValue),
                Arg.Is<double?>(l => l.HasValue && Math.Abs(l.Value - 24962) < 0.01),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Logger.Successes.Should().Contain(m => m.Contains("BRACKET UPDATED") && m.Contains("incident") && m.Contains("qty=6"));
            h.Network.Received(1).SendTradeLog("incident", "NT:ORDER", Arg.Is<string>(s => s.Contains("Bracket updated") && s.Contains("qty=6")));

            h.Tracker.TryGetStopLoss("incident", out var trackedStop).Should().BeTrue();
            trackedStop.Quantity.Should().Be(6);
            trackedStop.StopPrice.Should().BeApproximately(25022, 0.01);
            h.Tracker.TryGetTakeProfit("incident", out var trackedTarget).Should().BeTrue();
            trackedTarget.Quantity.Should().Be(6);
            trackedTarget.LimitPrice.Should().BeApproximately(24962, 0.01);

            // Both entry fills were still reported to Python.
            h.Network.Received(2).SendEntryFill("incident", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void FullFill_WhenBracketLegsTerminal_RecreatesBracketImmediately()
        {
            // Modify fails AND both tracked legs are already terminal (a genuinely dead
            // bracket): recreating cannot produce live duplicates, so a fresh bracket is
            // created immediately — no deferral, no flatten.

            var h = new ConnectorHarness(cashValue: 9000);
            h.TradeIdExtractor.ExtractTradeId("Entry_dead").Returns("dead");
            h.TradeIdExtractor.IsEntryOrder("Entry_dead").Returns(true);

            var entryPartial = TestDataFactory.Order(name: "Entry_dead", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1, quantity: 3, instrument: h.Instrument, avgFill: 21000);
            var entryFull = TestDataFactory.Order(name: "Entry_dead", side: OrderSide.Buy, state: OrderState.Filled, filled: 3, quantity: 3, instrument: h.Instrument, avgFill: 21002);
            var stop1 = TestDataFactory.Order(name: "Stop_dead", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: h.Instrument, stopPrice: 20970, orderType: OrderType.StopMarket);
            var target1 = TestDataFactory.Order(name: "Target_dead", side: OrderSide.Sell, state: OrderState.Working, quantity: 1, instrument: h.Instrument, limitPrice: 21060, orderType: OrderType.Limit);
            var stop2 = TestDataFactory.Order(name: "Stop_dead", side: OrderSide.Sell, state: OrderState.Initialized, quantity: 3, instrument: h.Instrument, stopPrice: 20972, orderType: OrderType.StopMarket);
            var target2 = TestDataFactory.Order(name: "Target_dead", side: OrderSide.Sell, state: OrderState.Initialized, quantity: 3, instrument: h.Instrument, limitPrice: 21062, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.Buy, 3, "dead").Returns(entryPartial);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "dead").Returns(stop1, stop2);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "dead").Returns(target1, target2);
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => throw new InvalidOperationException("NT rejected amend"));

            h.Open("dead", "long", 3, riskPoints: 30).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 21000, 1);
            h.Execution.Received(1).CreateStopLossOrder(h.Instrument, h.Account, OrderSide.Sell, 1, 20970, "dead");

            // Both legs die (e.g. rejected by the broker) before the full fill arrives.
            h.Tracker.TrackStopLoss("dead", stop1.WithState(OrderState.Cancelled));
            h.Tracker.TrackTakeProfit("dead", target1.WithState(OrderState.Cancelled));

            h.Service.OnExecutionUpdate(entryFull, 21002, 2);

            // Exactly one fresh bracket created+submitted; the dead legs are not cancelled.
            h.Execution.Received(2).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "dead");
            h.Execution.Received(2).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "dead");
            h.Execution.Received(2).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());
            h.Execution.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
            h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
            h.Network.DidNotReceive().SendError("ninjatrader", "bracket_creation_failed", Arg.Any<string>());
            h.Logger.Warnings.Should().Contain(m => m.Contains("is dead") && m.Contains("dead") && m.Contains("recreating"));
            h.Network.Received(2).SendEntryFill("dead", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void MultiplePartialFills_LastAmendmentWins()
        {
            // Three fill chunks with the amend rejected each time: the pending amendment
            // is overwritten on every fill; when the legs reach Working a single amend
            // pair applies with the FINAL cumulative qty/prices.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("multi");

            var entryOrder = TestDataFactory.Order(name: "Entry_multi", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryP1 = TestDataFactory.Order(name: "Entry_multi", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 2, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryP2 = TestDataFactory.Order(name: "Entry_multi", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25001);
            var entryFull = TestDataFactory.Order(name: "Entry_multi", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_multi", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 2, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_multi", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 2, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "multi").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "multi").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "multi").Returns(targetInit);

            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not modifiable"); });

            h.Open("multi", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryP1, 25000, 2);
            h.Service.OnExecutionUpdate(entryP2, 25001, 2);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);

            // Exactly one bracket generation; the amendment was deferred on each later fill.
            h.Execution.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "multi");
            h.Execution.Received(1).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "multi");
            h.Execution.Received(1).SubmitOrders(Arg.Any<IReadOnlyList<BrokerOrder>>());
            h.Execution.DidNotReceiveWithAnyArgs().CancelOrder(Arg.Any<BrokerOrder>());
            h.Logger.Warnings.FindAll(m => m.Contains("BRACKET AMENDMENT PENDING")).Count.Should().Be(2);

            modifiable = true;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));

            // A single amend pair, carrying the LAST amendment (avg 25002 → SL 25022 / TP 24962, qty 6).
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_multi" && o.OrderState == OrderState.Working),
                Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 25022) < 0.01),
                Arg.Is<double?>(l => !l.HasValue),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Target_multi" && o.OrderState == OrderState.Working),
                Arg.Is<double?>(s => !s.HasValue),
                Arg.Is<double?>(l => l.HasValue && Math.Abs(l.Value - 24962) < 0.01),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));

            h.Tracker.TryGetStopLoss("multi", out var trackedStop).Should().BeTrue();
            trackedStop.Quantity.Should().Be(6);
            h.Network.Received(3).SendEntryFill("multi", Arg.Any<double>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>());
        }

        [Fact]
        public void DeferredAmendment_RetriesOnNextUpdate_WhenApplyFails()
        {
            // The first apply attempt (on Submitted) fails again — the amendment stays
            // pending and a later Working update applies it; exactly one successful
            // amend pair overall.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("retry");

            var entryOrder = TestDataFactory.Order(name: "Entry_retry", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_retry", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_retry", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_retry", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_retry", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "retry").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "retry").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "retry").Returns(targetInit);

            bool failModify = true;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (failModify) throw new InvalidOperationException("rejected"); });

            h.Open("retry", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);
            h.Logger.Warnings.Should().Contain(m => m.Contains("BRACKET AMENDMENT PENDING"));

            // First apply attempt (legs Submitted) fails again — amendment stays pending.
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Submitted));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Submitted));

            h.Logger.Warnings.Should().Contain(m => m.Contains("Failed to apply pending bracket amendment") && m.Contains("retry"));
            h.Tracker.TryGetStopLoss("retry", out var stopAfterFail).Should().BeTrue();
            stopAfterFail.Quantity.Should().Be(4);

            // A later Working update applies it.
            failModify = false;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));

            // Exactly one successful amend pair overall (qty 6 at the amended prices).
            // The apply fires on the stop's Working update — at that point the tracked
            // target is still Submitted, which is already a modifiable state.
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_retry" && o.OrderState == OrderState.Working),
                Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 25022) < 0.01),
                Arg.Is<double?>(l => !l.HasValue),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Target_retry"),
                Arg.Is<double?>(s => !s.HasValue),
                Arg.Is<double?>(l => l.HasValue && Math.Abs(l.Value - 24962) < 0.01),
                Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Tracker.TryGetStopLoss("retry", out var trackedStop).Should().BeTrue();
            trackedStop.Quantity.Should().Be(6);
            h.Logger.Successes.Should().Contain(m => m.Contains("BRACKET UPDATED") && m.Contains("retry"));
        }

        [Fact]
        public void PendingAmendment_Discarded_WhenTradeClosesBeforeApply()
        {
            // Amendment pending; the stop fills fully before the legs become modifiable.
            // The trade is removed and the amendment discarded — subsequent order updates
            // must trigger no ModifyOrder, no recreate, no guard action.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("gone");

            var entryOrder = TestDataFactory.Order(name: "Entry_gone", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_gone", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_gone", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_gone", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_gone", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "gone").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "gone").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "gone").Returns(targetInit);
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => throw new InvalidOperationException("not modifiable"));

            h.Open("gone", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);
            h.Service.OnExecutionUpdate(entryFull, 25002, 2);
            h.Logger.Warnings.Should().Contain(m => m.Contains("BRACKET AMENDMENT PENDING"));

            // The qty=4 stop sweeps and fills completely before NT makes the legs modifiable.
            var stopFill = TestDataFactory.Order(name: "Stop_gone", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 4, quantity: 4, instrument: h.Instrument, avgFill: 25020, stopPrice: 25020, orderType: OrderType.StopMarket);
            h.Service.OnExecutionUpdate(stopFill, 25020, 4);

            h.Logger.Infos.Should().Contain(m => m.Contains("Discarding pending bracket amendment") && m.Contains("gone"));
            h.Tracker.TryGetEntry("gone", out _).Should().BeFalse();
            h.Tracker.TryGetStopLoss("gone", out _).Should().BeFalse();
            h.Tracker.TryGetTakeProfit("gone", out _).Should().BeFalse();
            h.Network.Received(1).SendExitFill("gone", 25020, "SL", Arg.Any<string>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<double?>());

            // Late order updates must not resurrect the amendment.
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));

            // The only amend attempt ever was the failed in-place one at the second fill.
            h.Execution.Received(1).ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>());
            h.Execution.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "gone");
            h.Execution.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), Arg.Any<BrokerAccount>(), Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<string>());
        }

        [Fact]
        public void DeferredAmendment_ShortSide_UsesBuyToCover()
        {
            // Mirrors the incident direction (short entry): the created bracket legs and
            // the legs the deferred amendment applies to are all BuyToCover.

            var h = new ConnectorHarness();
            h.MockTradeIdNames("side");

            var entryOrder = TestDataFactory.Order(name: "Entry_side", side: OrderSide.SellShort, state: OrderState.Submitted, quantity: 6, instrument: h.Instrument);
            var entryPartial = TestDataFactory.Order(name: "Entry_side", side: OrderSide.SellShort, state: OrderState.PartFilled, filled: 4, quantity: 6, instrument: h.Instrument, avgFill: 25000);
            var entryFull = TestDataFactory.Order(name: "Entry_side", side: OrderSide.SellShort, state: OrderState.Filled, filled: 6, quantity: 6, instrument: h.Instrument, avgFill: 25002);
            var stopInit = TestDataFactory.Order(name: "Stop_side", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, stopPrice: 25020, orderType: OrderType.StopMarket);
            var targetInit = TestDataFactory.Order(name: "Target_side", side: OrderSide.BuyToCover, state: OrderState.Initialized, quantity: 4, instrument: h.Instrument, limitPrice: 24960, orderType: OrderType.Limit);

            h.Execution.CreateEntryOrder(h.Instrument, h.Account, OrderSide.SellShort, 6, "side").Returns(entryOrder);
            h.Execution.CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "side").Returns(stopInit);
            h.Execution.CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "side").Returns(targetInit);

            bool modifiable = false;
            h.Execution.When(x => x.ModifyOrder(Arg.Any<BrokerOrder>(), Arg.Any<double?>(), Arg.Any<double?>(), Arg.Any<int?>()))
                .Do(_ => { if (!modifiable) throw new InvalidOperationException("not modifiable"); });

            h.Open("side", "short", 6).Should().BeTrue();
            h.Service.OnExecutionUpdate(entryPartial, 25000, 4);

            // Bracket legs are BuyToCover for the short position.
            h.Execution.Received(1).CreateStopLossOrder(h.Instrument, h.Account, OrderSide.BuyToCover, 4, 25020, "side");
            h.Execution.Received(1).CreateTakeProfitOrder(h.Instrument, h.Account, OrderSide.BuyToCover, 4, 24960, "side");

            h.Service.OnExecutionUpdate(entryFull, 25002, 2);
            modifiable = true;
            h.Service.OnOrderUpdate(stopInit.WithState(OrderState.Working));
            h.Service.OnOrderUpdate(targetInit.WithState(OrderState.Working));

            // The deferred amendment applies to the BuyToCover legs; no second bracket.
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_side" && o.OrderSide == OrderSide.BuyToCover && o.OrderState == OrderState.Working),
                Arg.Any<double?>(), Arg.Any<double?>(), Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Execution.Received(1).ModifyOrder(
                Arg.Is<BrokerOrder>(o => o.Name == "Target_side" && o.OrderSide == OrderSide.BuyToCover && o.OrderState == OrderState.Working),
                Arg.Any<double?>(), Arg.Any<double?>(), Arg.Is<int?>(q => q.HasValue && q.Value == 6));
            h.Execution.Received(1).CreateStopLossOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "side");
            h.Execution.Received(1).CreateTakeProfitOrder(Arg.Any<BrokerInstrument>(), h.Account, Arg.Any<OrderSide>(), Arg.Any<int>(), Arg.Any<double>(), "side");

            h.Tracker.TryGetStopLoss("side", out var trackedStop).Should().BeTrue();
            trackedStop.OrderSide.Should().Be(OrderSide.BuyToCover);
            h.Tracker.TryGetTakeProfit("side", out var trackedTarget).Should().BeTrue();
            trackedTarget.OrderSide.Should().Be(OrderSide.BuyToCover);
        }
    }
}
