using System;
using System.Collections.Generic;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Tests
{
    /// <summary>
    /// Shared wiring for connector-level scenarios: real CommandDispatcher +
    /// OrderOpenHandler + ConnectorService + InMemoryOrderTracker, with the broker
    /// boundary (IOrderExecutionService) and all other ports mocked.
    /// </summary>
    public sealed class ConnectorHarness
    {
        public IZmqNetwork Network { get; } = Substitute.For<IZmqNetwork>();
        public TestLogger Logger { get; } = new TestLogger();
        public InMemoryOrderTracker Tracker { get; } = new InMemoryOrderTracker();
        public CommandDispatcher Dispatcher { get; }
        public IAccountProvider AccountProvider { get; } = Substitute.For<IAccountProvider>();
        public IInstrumentProvider InstrumentProvider { get; } = Substitute.For<IInstrumentProvider>();
        public IOrderExecutionService Execution { get; } = Substitute.For<IOrderExecutionService>();
        public ITradeIdExtractor TradeIdExtractor { get; } = Substitute.For<ITradeIdExtractor>();
        public IConnectorClock Clock { get; } = Substitute.For<IConnectorClock>();
        public ConnectorService Service { get; }
        public BrokerAccount Account { get; }
        public BrokerInstrument Instrument { get; }

        public ConnectorHarness(double cashValue = 50000)
        {
            Dispatcher = new CommandDispatcher(Logger);
            Account = TestDataFactory.Account(cashValue: cashValue);
            Instrument = TestDataFactory.Instrument();
            AccountProvider.GetAccounts().Returns(new List<BrokerAccount> { Account });
            AccountProvider.GetAccount("Sim101").Returns(Account);
            InstrumentProvider.GetInstrument("MNQ 09-25").Returns(Instrument);
            Clock.UtcNow.Returns(DateTime.UtcNow);

            Dispatcher.Register(new OrderOpenHandler(Network, Logger, Tracker, AccountProvider, InstrumentProvider, Execution));

            Service = new ConnectorService(
                TestDataFactory.Config(), Network, Logger, Dispatcher, Tracker,
                Substitute.For<IStreamingCoordinator>(), AccountProvider, Execution, InstrumentProvider,
                Substitute.For<IBarHistoryService>(), Substitute.For<IPnLCalculator>(), Clock, TradeIdExtractor);
        }

        /// <summary>Registers the close/modify handlers for dispatcher-driven close flows.</summary>
        public void RegisterCloseHandler()
        {
            Dispatcher.Register(new OrderCloseHandler(Network, Logger, Tracker, TradeIdExtractor, AccountProvider, InstrumentProvider, Execution));
        }

        /// <summary>Opens a trade through the real ORDER_OPEN pipeline; qty comes from risk_usd = contracts × riskPoints × pointValue.</summary>
        public bool Open(string tradeId, string direction, int contracts, double riskPoints = 20, double rrRatio = 2)
        {
            return Dispatcher.Dispatch(MessageEnvelope.Create(MessageType.OrderOpen,
                TestDataFactory.OrderOpenPayload(
                    tradeId: tradeId, direction: direction,
                    riskUsd: contracts * riskPoints * Instrument.PointValue,
                    riskPoints: riskPoints, rrRatio: rrRatio, account: "Sim101")));
        }

        /// <summary>Teaches the trade-id extractor the standard Entry_/Stop_/Target_/Close_ naming for a trade.</summary>
        public void MockTradeIdNames(string tradeId)
        {
            TradeIdExtractor.ExtractTradeId($"Entry_{tradeId}").Returns(tradeId);
            TradeIdExtractor.ExtractTradeId($"Stop_{tradeId}").Returns(tradeId);
            TradeIdExtractor.ExtractTradeId($"Target_{tradeId}").Returns(tradeId);
            TradeIdExtractor.ExtractTradeId($"Close_{tradeId}").Returns(tradeId);
            TradeIdExtractor.IsEntryOrder($"Entry_{tradeId}").Returns(true);
            TradeIdExtractor.IsStopOrder($"Stop_{tradeId}").Returns(true);
            TradeIdExtractor.IsTargetOrder($"Target_{tradeId}").Returns(true);
            TradeIdExtractor.IsCloseOrder($"Close_{tradeId}").Returns(true);
        }
    }
}
