using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    /// <summary>
    /// Tests for the health monitor: the command watchdog (hung Dispatch), the
    /// data-flow watchdog (subscribed but silent), and the connection recovery
    /// they escalate to. Recovery must self-heal the connector without the user
    /// clicking Connect.
    /// </summary>
    public class ConnectorServiceRecoveryTests
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

        public ConnectorServiceRecoveryTests()
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

            // Keep the connection (ping) watchdog quiet so only the health monitor
            // under test can restart the sockets.
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(true);
        }

        private ConnectorService CreateService(ZmqConfiguration config = null)
        {
            var service = new ConnectorService(
                config ?? _config, _network, _logger, _dispatcher, _orderTracker,
                _streamingCoordinator, _accountProvider, _orderExecutionService,
                _instrumentProvider, _barHistoryService, _pnlCalculator, _clock, _tradeIdExtractor,
                watchdogIntervalMs: 30, watchdogFailureThreshold: 3, watchdogPingTimeoutMs: 50);
            service.SafetyGuardEnabled = false;
            return service;
        }

        private sealed class BlockingHandler : ICommandHandler
        {
            private readonly ManualResetEventSlim _gate;

            public BlockingHandler(ManualResetEventSlim gate)
            {
                _gate = gate;
            }

            public string CommandType => "subscribe";

            public bool Handle(JObject payload)
            {
                _gate.Wait(TimeSpan.FromSeconds(30)); // cap so a failing test leaks no hung thread
                return true;
            }
        }

        private sealed class FastHandler : ICommandHandler
        {
            public string CommandType => "subscribe";
            public bool Handle(JObject payload) => true;
        }

        /// <summary>Signals when SendConnect is called for the second time (Connect + recovery).</summary>
        private ManualResetEventSlim TrackRecoveryConnect()
        {
            int connects = 0;
            var recoveryConnect = new ManualResetEventSlim(false);
            _network.When(x => x.SendConnect(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>()))
                .Do(_ => { if (Interlocked.Increment(ref connects) >= 2) recoveryConnect.Set(); });
            return recoveryConnect;
        }

        [Fact]
        public void CommandWatchdog_HungDispatch_SendsFailureAck_AndTriggersRecovery()
        {
            var gate = new ManualResetEventSlim(false);
            _dispatcher.Register(new BlockingHandler(gate));
            var envelope = MessageEnvelope.Create("subscribe", TestDataFactory.SubscribePayload("MES 09-26"), seqNum: 5);
            int receives = 0;
            _network.ReceiveCommand(Arg.Any<int>())
                .Returns(_ => Interlocked.Increment(ref receives) == 1 ? envelope : null);

            var timeoutAcked = new ManualResetEventSlim(false);
            _network.When(x => x.SendCommandAck(Arg.Any<string>(), Arg.Any<int>(), Arg.Any<bool>(), Arg.Any<string>(), Arg.Any<string>()))
                .Do(ci => { if ((string)ci[4] == "command timeout") timeoutAcked.Set(); });
            var recoveryConnect = TrackRecoveryConnect();

            _streamingCoordinator.SubscribedInstruments.Returns(new[] { "MNQ 09-25" });
            // IsStreaming stays false so the data-flow watchdog cannot interfere.
            var service = CreateService(new ZmqConfiguration(commandTimeoutSeconds: 1));

            service.Connect();
            try
            {
                timeoutAcked.Wait(TimeSpan.FromSeconds(10)).Should().BeTrue(
                    "a command stuck in Dispatch longer than CommandTimeoutSeconds must be ACKed as failed");
                recoveryConnect.Wait(TimeSpan.FromSeconds(10)).Should().BeTrue(
                    "a stuck command must trigger connection recovery");

                _network.Received().SendCommandAck("subscribe", 5, false, Arg.Any<string>(), "command timeout");
                _streamingCoordinator.Received(1).Stop();
                _network.Received(1).Stop();
                _network.Received(2).Start(); // Connect() + recovery
                _streamingCoordinator.Received(1).Start("MNQ 09-25");
                _logger.Errors.Should().Contain(e => e.Message.Contains("COMMAND WATCHDOG"));
            }
            finally
            {
                gate.Set(); // release the hung handler so the command thread can exit
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void DataWatchdog_SilentStream_TriggersRecovery_AfterSecondThresholdInterval()
        {
            int streamingFlag = 1;
            _streamingCoordinator.IsStreaming.Returns(_ => Volatile.Read(ref streamingFlag) == 1);
            _streamingCoordinator.GetStats().Returns((3L, 2L, 1L)); // frozen counters
            _streamingCoordinator.SubscribedInstruments.Returns(new[] { "MNQ 09-25" });
            var recoveryConnect = TrackRecoveryConnect();
            var service = CreateService(new ZmqConfiguration(dataFlowSilenceThresholdSeconds: 1));

            service.Connect();
            try
            {
                recoveryConnect.Wait(TimeSpan.FromSeconds(15)).Should().BeTrue(
                    "frozen stats while streaming must escalate to recovery after two threshold intervals");
                Volatile.Write(ref streamingFlag, 0); // stop further data-watchdog recoveries

                _logger.Warnings.Should().Contain(w => w.Contains("DATA WATCHDOG"));
                _streamingCoordinator.Received(1).Stop();
                _network.Received(1).Stop();
                _network.Received(2).Start();
                _streamingCoordinator.Received(1).Start("MNQ 09-25");
                _network.Received(2).SendConnect("ninjatrader", Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void Recovery_IsIdempotent_ConcurrentTriggers_RunOneRecovery()
        {
            var stopEntered = new ManualResetEventSlim(false);
            var releaseStop = new ManualResetEventSlim(false);
            _streamingCoordinator.When(x => x.Stop())
                .Do(_ => { stopEntered.Set(); releaseStop.Wait(TimeSpan.FromSeconds(10)); });
            var recoveryConnect = TrackRecoveryConnect();
            var service = CreateService();

            service.Connect();
            try
            {
                var trigger = typeof(ConnectorService).GetMethod("TriggerRecovery", BindingFlags.NonPublic | BindingFlags.Instance);
                trigger.Should().NotBeNull();

                // TriggerRecovery is non-blocking (guard + thread.Start), so invoke both
                // synchronously. The second invoke MUST happen while the first recovery is
                // still blocked inside Stop() — firing it on a pooled thread races with the
                // first recovery's completion and would legitimately start a second recovery.
                trigger.Invoke(service, new object[] { "first" });
                stopEntered.Wait(TimeSpan.FromSeconds(5)).Should().BeTrue("first recovery must be inside Stop() before the second trigger");
                trigger.Invoke(service, new object[] { "second" });
                releaseStop.Set();

                recoveryConnect.Wait(TimeSpan.FromSeconds(5)).Should().BeTrue("the single recovery must complete");
                _streamingCoordinator.Received(1).Stop();
                _network.Received(1).Stop();
                _network.Received(2).Start();
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void Recovery_Exception_IsLogged_AndRetriedOnce()
        {
            int streamingFlag = 1;
            _streamingCoordinator.IsStreaming.Returns(_ => Volatile.Read(ref streamingFlag) == 1);
            _streamingCoordinator.GetStats().Returns((3L, 2L, 1L));
            _streamingCoordinator.SubscribedInstruments.Returns(new string[0]);

            int starts = 0;
            _network.When(x => x.Start())
                .Do(_ => { if (Interlocked.Increment(ref starts) > 1) throw new InvalidOperationException("boom"); });

            var service = CreateService(new ZmqConfiguration(dataFlowSilenceThresholdSeconds: 1));
            service.RecoveryRetryDelayMs = 100;

            service.Connect();
            try
            {
                bool retried = SpinWait.SpinUntil(() => Volatile.Read(ref starts) >= 3, TimeSpan.FromSeconds(15));
                retried.Should().BeTrue("a failed recovery must be retried once (Connect + 2 failed recovery Starts)");
                Volatile.Write(ref streamingFlag, 0);

                _network.Received(3).Start();
                _logger.Errors.Should().Contain(e => e.Message.Contains("Connection recovery failed"));
                _logger.Errors.Should().Contain(e => e.Message.Contains("Connection recovery retry failed"));
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void CommandWatchdog_FastDispatch_DoesNotTriggerRecovery()
        {
            _dispatcher.Register(new FastHandler());
            var envelope = MessageEnvelope.Create("subscribe", TestDataFactory.SubscribePayload("MNQ 09-25"), seqNum: 7);
            int receives = 0;
            _network.ReceiveCommand(Arg.Any<int>())
                .Returns(_ => Interlocked.Increment(ref receives) == 1 ? envelope : null);

            var acked = new ManualResetEventSlim(false);
            _network.When(x => x.SendCommandAck(Arg.Any<string>(), Arg.Any<int>(), true, Arg.Any<string>(), Arg.Any<string>()))
                .Do(_ => acked.Set());
            var service = CreateService(new ZmqConfiguration(commandTimeoutSeconds: 1));

            service.Connect();
            try
            {
                acked.Wait(TimeSpan.FromSeconds(5)).Should().BeTrue("a fast dispatch is ACKed as success");
                Thread.Sleep(2000); // well beyond the 1s command timeout

                _network.Received(1).Start(); // only Connect()
                _network.DidNotReceive().Stop();
                _streamingCoordinator.DidNotReceive().Stop();
                _network.Received(1).SendConnect("ninjatrader", Arg.Any<string>(), Arg.Any<string>(), Arg.Any<string>());
                _logger.Errors.Should().NotContain(e => e.Message.Contains("COMMAND WATCHDOG"));
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }
    }
}
