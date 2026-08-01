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
        public static ConnectorService Build(ZmqConfiguration config, ILogger logger)
        {
            var serializer = new JsonMessageSerializer(logger);
            var network = new ZmqNetwork(config, serializer, logger);

            var accountProvider = new NtAccountProvider();
            var instrumentProvider = new NtInstrumentProvider();
            var orderExecutionService = new NtOrderExecutionService(instrumentProvider, accountProvider);
            var orderTracker = new NtOrderTracker();
            var streamingCoordinator = new NtStreamingCoordinator(network, logger, config);
            var barHistoryService = new NtBarHistoryService(instrumentProvider, logger);
            var pnlCalculator = new NtPnLCalculator();
            var clock = new NtConnectorClock();
            var tradeIdExtractor = new NtTradeIdExtractor();

            var dispatcher = new CommandDispatcher(logger);
            dispatcher.Register(new SubscribeHandler(network, logger, streamingCoordinator));
            dispatcher.Register(new UnsubscribeHandler(network, logger, streamingCoordinator));
            dispatcher.Register(new OrderOpenHandler(network, logger, orderTracker, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new OrderCloseHandler(network, logger, orderTracker, tradeIdExtractor, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new OrderModifyHandler(network, logger, orderTracker, tradeIdExtractor, accountProvider, instrumentProvider, orderExecutionService));
            dispatcher.Register(new RefreshRequestHandler(network, logger, instrumentProvider, barHistoryService, clock, config));
            dispatcher.Register(new AuditRequestHandler(network, logger, instrumentProvider, barHistoryService, streamingCoordinator, clock));

            var connectorService = new ConnectorService(
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

            // Registered after the service exists: the handler needs the service to
            // act on a platform-requested disconnect.
            dispatcher.Register(new DisconnectHandler(network, logger, connectorService));

            return connectorService;
        }
    }
}
