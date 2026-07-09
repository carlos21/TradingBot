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
    }
}
