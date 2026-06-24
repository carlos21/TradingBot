using TradingBot.NinjaTrader.AddOn.Infrastructure;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Presentation
{
    /// <summary>
    /// Composition root: wires all NinjaTrader-specific implementations to the
    /// headless ConnectorService. This is the only place that knows about both
    /// NinjaTrader and application layers.
    /// </summary>
    public static class NinjaTraderCompositionRoot
    {
        public static ConnectorService Build(ZmqConfiguration config, ILogger logger, bool simulateTrades)
        {
            var serializer = new JsonMessageSerializer(logger);
            var network = new ZmqNetwork(config, serializer, logger);

            var accountProvider = new NtAccountProvider();
            var instrumentProvider = new NtInstrumentProvider();
            var orderExecutionService = new NtOrderExecutionService(instrumentProvider, accountProvider);
            var orderTracker = new NtOrderTracker();
            var streamingCoordinator = new NtStreamingCoordinator(network, logger, config);
            var barHistoryService = new NtBarHistoryService(instrumentProvider);
            var pnlCalculator = new NtPnLCalculator();
            var clock = new NtConnectorClock();
            var tradeIdExtractor = new NtTradeIdExtractor();
            var tradingMode = new SimulationTradingMode(simulateTrades);

            var dispatcher = new CommandDispatcher(logger);
            dispatcher.Register(new SubscribeHandler(network, logger, streamingCoordinator));
            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new OrderCloseHandler(network, logger, orderTracker, tradeIdExtractor, tradingMode, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new OrderModifyHandler(network, logger, orderTracker, tradeIdExtractor, tradingMode, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new RefreshRequestHandler(network, logger, instrumentProvider, barHistoryService, clock, config));
            dispatcher.Register(new AuditRequestHandler(network, logger, instrumentProvider, barHistoryService, streamingCoordinator, clock));
            dispatcher.Register(new TestStartHandler(network, logger));

            return new ConnectorService(
                config,
                network,
                logger,
                dispatcher,
                orderTracker,
                streamingCoordinator,
                accountProvider,
                orderExecutionService,
                instrumentProvider,
                barHistoryService,
                pnlCalculator,
                clock,
                tradeIdExtractor);
        }
    }
}
