using System;
using System.Threading;
using System.Threading.Tasks;
using FluentAssertions;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    /// <summary>
    /// Tests for the connection watchdog: it pings Python on the query channel and
    /// recreates the ZMQ sockets after too many consecutive failures, so a dead
    /// (half-open) command channel recovers without restarting NinjaTrader.
    /// </summary>
    public class ConnectorServiceWatchdogTests
    {
        private readonly ZmqConfiguration _config;
        private readonly IZmqNetwork _network;
        private readonly TestLogger _logger;
        private readonly CommandDispatcher _dispatcher;
        private readonly InMemoryOrderTracker _orderTracker;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly IAccountProvider _accountProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IPnLCalculator _pnlCalculator;
        private readonly IConnectorClock _clock;
        private readonly ITradeIdExtractor _tradeIdExtractor;

        public ConnectorServiceWatchdogTests()
        {
            _config = TestDataFactory.Config();
            _network = Substitute.For<IZmqNetwork>();
            _logger = new TestLogger();
            _dispatcher = new CommandDispatcher(_logger);
            _orderTracker = new InMemoryOrderTracker();
            _streamingCoordinator = Substitute.For<IStreamingCoordinator>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _orderExecutionService = Substitute.For<IOrderExecutionService>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _barHistoryService = Substitute.For<IBarHistoryService>();
            _pnlCalculator = Substitute.For<IPnLCalculator>();
            _clock = Substitute.For<IConnectorClock>();
            _tradeIdExtractor = Substitute.For<ITradeIdExtractor>();

            _clock.UtcNow.Returns(new DateTime(2025, 6, 27, 12, 0, 0, DateTimeKind.Utc));
            _clock.Now.Returns(new DateTime(2025, 6, 27, 8, 0, 0, DateTimeKind.Local));
            _clock.When(x => x.Sleep(Arg.Any<int>())).Do(x => { });
            _clock.Delay(Arg.Any<int>(), Arg.Any<CancellationToken>()).Returns(Task.CompletedTask);
        }

        private ConnectorService CreateService(
            int watchdogIntervalMs = 30,
            int watchdogFailureThreshold = 3,
            int watchdogPingTimeoutMs = 50)
        {
            var service = new ConnectorService(
                _config, _network, _logger, _dispatcher, _orderTracker,
                _streamingCoordinator, _accountProvider, _orderExecutionService,
                _instrumentProvider, _barHistoryService, _pnlCalculator, _clock, _tradeIdExtractor,
                watchdogIntervalMs, watchdogFailureThreshold, watchdogPingTimeoutMs);
            service.SafetyGuardEnabled = false;
            return service;
        }

        [Fact]
        public void Watchdog_DoesNotRestartSockets_WhenPingSucceeds()
        {
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(true);
            var service = CreateService();

            service.Connect();
            try
            {
                Thread.Sleep(300);
                _network.DidNotReceive().Stop();
                _network.Received(1).Start(); // only the Connect() one
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void Watchdog_RestartsSockets_AfterThresholdConsecutiveFailures()
        {
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(false);
            var stopped = new ManualResetEventSlim(false);
            _network.When(x => x.Stop()).Do(_ => stopped.Set());
            var service = CreateService();

            service.Connect();
            try
            {
                stopped.Wait(TimeSpan.FromSeconds(2)).Should().BeTrue("watchdog should recreate sockets after 3 failed pings");
                _network.Received(1).Stop();
                _network.Received(2).Start(); // Connect() + watchdog recreation
                _logger.Warnings.Should().Contain(w => w.Contains("recreating ZMQ sockets"));
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void Watchdog_SuccessfulPing_ResetsFailureStreak()
        {
            // Fail, fail, success, then fail forever. With reset semantics the first
            // restart can only happen at ping #6 (fail, fail, success, fail, fail, fail).
            int pings = 0;
            _network.SendTestPingWithResponse(Arg.Any<double>())
                .Returns(_ => Interlocked.Increment(ref pings) == 3);

            int pingsAtStop = 0;
            var stopped = new ManualResetEventSlim(false);
            _network.When(x => x.Stop()).Do(_ => { pingsAtStop = pings; stopped.Set(); });
            var service = CreateService();

            service.Connect();
            try
            {
                stopped.Wait(TimeSpan.FromSeconds(2)).Should().BeTrue("watchdog should eventually restart on sustained failures");
                pingsAtStop.Should().BeGreaterThanOrEqualTo(6,
                    "the success at ping #3 must reset the streak, so the restart waits for 3 new consecutive failures");
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void Watchdog_StopsCleanly_OnDisconnect()
        {
            int pings = 0;
            _network.SendTestPingWithResponse(Arg.Any<double>())
                .Returns(_ => { Interlocked.Increment(ref pings); return false; });
            // High threshold: watchdog keeps pinging but never restarts the sockets.
            var service = CreateService(watchdogIntervalMs: 20, watchdogFailureThreshold: 1000);

            service.Connect();
            Thread.Sleep(150);
            service.Disconnect("cleanup");

            int pingsAfterDisconnect = pings;
            Thread.Sleep(150);
            pings.Should().Be(pingsAfterDisconnect, "watchdog loop must stop after Disconnect");
            _network.Received(1).Stop(); // only the Disconnect() one
        }
    }
}
