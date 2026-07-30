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

            // Wait on SubmitOrder — the LAST effect of the guard chain (SendError →
            // CreateMarketCloseOrder → SubmitOrder). Waiting on CreateMarketCloseOrder
            // would let the assertion thread wake before SubmitOrder ran. A second
            // flatten cannot happen: FlattenPosition tracks the close order and
            // removes the tracker entry, so the next cycle skips this trade.
            var flattened = new ManualResetEventSlim(false);
            orderExecutionService.When(x => x.SubmitOrder(closeOrder))
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
                flattened.Wait(TimeSpan.FromSeconds(5)).Should().BeTrue("safety guard should flatten after stop disappears");
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
            // If the broker rejects the in-place amend, the connector must fall back to
            // the old cancel+recreate path — and must NOT flatten when recreate succeeds.

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
            service.OnExecutionUpdate(entryFull, 21002, 2);

            // Fallback: the working bracket legs were cancelled and a fresh bracket submitted.
            orderExecutionService.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Stop_replay"));
            orderExecutionService.Received(1).CancelOrder(Arg.Is<BrokerOrder>(o => o.Name == "Target_replay"));
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
    }
}
