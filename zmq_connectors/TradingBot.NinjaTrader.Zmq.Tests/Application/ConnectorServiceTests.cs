using System;
using System.Collections.Generic;
using System.Linq;
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
    public class ConnectorServiceTests
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

        public ConnectorServiceTests()
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

        private ConnectorService CreateService()
        {
            var service = new ConnectorService(
                _config, _network, _logger, _dispatcher, _orderTracker,
                _streamingCoordinator, _accountProvider, _orderExecutionService,
                _instrumentProvider, _barHistoryService, _pnlCalculator, _clock, _tradeIdExtractor);
            service.SafetyGuardEnabled = false;
            return service;
        }

        [Theory]
        [InlineData(0, "config")]
        [InlineData(1, "network")]
        [InlineData(2, "logger")]
        [InlineData(3, "dispatcher")]
        [InlineData(4, "orderTracker")]
        [InlineData(5, "streamingCoordinator")]
        [InlineData(6, "accountProvider")]
        [InlineData(7, "orderExecutionService")]
        [InlineData(8, "instrumentProvider")]
        [InlineData(9, "barHistoryService")]
        [InlineData(10, "pnlCalculator")]
        [InlineData(11, "clock")]
        [InlineData(12, "tradeIdExtractor")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var deps = new object[]
            {
                _config, _network, _logger, _dispatcher, _orderTracker,
                _streamingCoordinator, _accountProvider, _orderExecutionService,
                _instrumentProvider, _barHistoryService, _pnlCalculator, _clock, _tradeIdExtractor
            };
            deps[nullIndex] = null;
            var args = deps.Concat(new object[] { Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing }).ToArray();
            var ctor = typeof(ConnectorService).GetConstructor(new[]
            {
                typeof(ZmqConfiguration), typeof(IZmqNetwork), typeof(ILogger), typeof(CommandDispatcher), typeof(IOrderTracker),
                typeof(IStreamingCoordinator), typeof(IAccountProvider), typeof(IOrderExecutionService), typeof(IInstrumentProvider),
                typeof(IBarHistoryService), typeof(IPnLCalculator), typeof(IConnectorClock), typeof(ITradeIdExtractor),
                typeof(int), typeof(int), typeof(int), typeof(int), typeof(int)
            });

            Action act = () => ctor.Invoke(args);
            act.Should().Throw<TargetInvocationException>().WithInnerException<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void Connect_StartsNetworkAndThreads_WhenNotConnected()
        {
            var service = CreateService();
            service.Connect();

            service.IsConnected.Should().BeTrue();
            _network.Received(1).Start();
            _network.Received(1).SendConnect("ninjatrader", _config.PlatformVersion, pair: "", account: null);

            service.Disconnect("cleanup");
        }

        [Fact]
        public void Connect_IgnoresSecondConnect()
        {
            var service = CreateService();
            service.Connect();
            service.Connect();

            _network.Received(1).Start();
            _logger.Warnings.Should().Contain(w => w.Contains("Already connected"));

            service.Disconnect("cleanup");
        }

        [Fact]
        public void Network_Property_Should_Return_Network_Instance()
        {
            var service = CreateService();
            service.Network.Should().BeSameAs(_network);
        }

        [Fact]
        public void Disconnect_AlwaysStopsNetwork_AndClearsState()
        {
            var service = CreateService();
            service.Disconnect("cleanup");

            service.IsConnected.Should().BeFalse();
            _network.Received(1).Stop();
            _orderTracker.GetActiveTradeIds().Should().BeEmpty();
        }

        [Fact]
        public void Disconnect_StopsNetworkAndCleansState()
        {
            var service = CreateService();
            service.Connect();
            service.Disconnect("test done");

            service.IsConnected.Should().BeFalse();
            _network.Received(1).Stop();
            _orderTracker.GetActiveTradeIds().Should().BeEmpty();
        }

        [Fact]
        public void Dispose_CallsDisconnect()
        {
            var service = CreateService();
            service.Connect();
            service.Dispose();

            service.IsConnected.Should().BeFalse();
            _network.Received(1).Stop();
        }

        [Fact]
        public void GetStats_ReturnsStreamingStatsAndCommandCount()
        {
            _streamingCoordinator.GetStats().Returns((10L, 5L, 2L));
            var service = CreateService();

            var stats = service.GetStats();

            stats.Should().Contain("Ticks: 10");
            stats.Should().Contain("Bars: 5");
            stats.Should().Contain("Partial: 2");
            stats.Should().Contain("Cmds: 0");
        }

        [Fact]
        public async Task TestConnectionAsync_ReturnsTrue_WhenPingSucceeds()
        {
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(true);
            var service = CreateService();

            var result = await service.TestConnectionAsync();

            result.Should().BeTrue();
            _logger.Successes.Should().Contain(s => s.Contains("PASSED"));
        }

        [Fact]
        public async Task TestConnectionAsync_ReturnsFalse_WhenPingFails()
        {
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(false);
            var service = CreateService();

            var result = await service.TestConnectionAsync();

            result.Should().BeFalse();
            _logger.Warnings.Should().Contain(w => w.Contains("FAILED"));
        }

        [Fact]
        public void RestoreOrderTracking_TracksEntryStopTargetAndCloseOrders()
        {
            var account = TestDataFactory.Account();
            var entry = TestDataFactory.Order(name: "Entry_t1", state: OrderState.Working);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working);
            var close = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAllOrders(account).Returns(new List<BrokerOrder> { entry, stop, target, close });
            _tradeIdExtractor.ExtractTradeId(Arg.Any<string>()).Returns(x => x.Arg<string>().Split('_')[1]);
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _tradeIdExtractor.IsCloseOrder("Close_t1").Returns(true);

            var service = CreateService();
            service.Connect();

            _orderTracker.TryGetEntry("t1", out _).Should().BeTrue();
            _orderTracker.TryGetStopLoss("t1", out _).Should().BeTrue();
            _orderTracker.TryGetTakeProfit("t1", out _).Should().BeTrue();
            _orderTracker.TryGetCloseOrder("t1", out _).Should().BeTrue();

            service.Disconnect("cleanup");
        }

        [Fact]
        public void ReportPositionsToPython_SendsPositionSync()
        {
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _orderTracker.TrackTakeProfit("t1", target);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());

            var service = CreateService();
            service.Connect();

            _network.Received(1).SendPositionSync(Arg.Is<JArray>(a => a.Count == 1), Arg.Any<JArray>());

            service.Disconnect("cleanup");
        }

        [Fact]
        public void OnOrderUpdate_TracksEntryOrder()
        {
            var service = CreateService();
            var order = TestDataFactory.Order(name: "Entry_t1", state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.OnOrderUpdate(order);

            _orderTracker.TryGetEntry("t1", out var tracked).Should().BeTrue();
            tracked.Name.Should().Be("Entry_t1");
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenEntryOrderMissingTradeId()
        {
            var service = CreateService();
            var order = TestDataFactory.Order(name: "Entry_", state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Entry_").Returns("");
            _tradeIdExtractor.IsEntryOrder("Entry_").Returns(true);

            service.OnOrderUpdate(order);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnOrderUpdate_RemovesTrade_WhenEntryRejected()
        {
            var service = CreateService();
            var order = TestDataFactory.Order(name: "Entry_t1", state: OrderState.Rejected);
            _orderTracker.TrackEntry("t1", order);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.OnOrderUpdate(order);

            _orderTracker.TryGetEntry("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnOrderUpdate_HandlesCancelledBracketOrder_WithPendingModify()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Cancelled, stopPrice: 19990);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:sl", new PendingModifyInfo(19980, instrument, OrderSide.Sell, 2));
            _orderTracker.ExpectCancellation("Stop_t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);
            var newStop = TestDataFactory.Order(name: "Stop_t1_v2", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19980);
            _orderExecutionService.CreateStopLossOrder(instrument, account, OrderSide.Sell, 2, 19980, "t1").Returns(newStop);

            service.OnOrderUpdate(stop);

            _orderExecutionService.Received(1).SubmitOrder(newStop);
            _orderTracker.TryGetStopLoss("t1", out var tracked).Should().BeTrue();
            tracked.StopPrice.Should().Be(19980);
        }

        [Fact]
        public void OnExecutionUpdate_HandlesEntryFill_AndCreatesBracket()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 2, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 2, 20020, "t1").Returns(target);

            service.OnExecutionUpdate(entry, 20000, 2);

            _orderExecutionService.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(list => list.Count == 2 && list.Contains(stop) && list.Contains(target)));
            _network.Received(1).SendEntryFill("t1", 20000, 19990, 20020, account: "Sim101", quantity: 2, accountBalance: account.CashValue);
        }

        [Fact]
        public void OnExecutionUpdate_HandlesStopLossFill()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _pnlCalculator.Calculate(entry, stop).Returns(new PnlResult(-20, 0));
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnExecutionUpdate(stop, 19990, 2);

            _network.Received(1).SendExitFill("t1", 19990, "SL", account: "Sim101", realizedPnl: -20, commission: 0);
            _orderTracker.TryGetStopLoss("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnExecutionUpdate_StopLossFill_SendsAccountBalance()
        {
            var service = CreateService();
            var account = TestDataFactory.Account(cashValue: 54321);
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _pnlCalculator.Calculate(entry, stop).Returns(new PnlResult(-20, 0));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnExecutionUpdate(stop, 19990, 2);

            _network.Received(1).SendExitFill("t1", 19990, "SL", account: "Sim101", realizedPnl: -20, commission: 0, accountBalance: 54321);
            _orderTracker.TryGetStopLoss("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnExecutionUpdate_HandlesTakeProfitFill()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackTakeProfit("t1", target);
            _pnlCalculator.Calculate(entry, target).Returns(new PnlResult(40, 0));
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnExecutionUpdate(target, 20040, 2);

            _network.Received(1).SendExitFill("t1", 20040, "TP", account: "Sim101", realizedPnl: 40, commission: 0);
            _orderTracker.TryGetTakeProfit("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnExecutionUpdate_ExitFill_IncludesEntryAndExitCommission()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            // The tracked entry (from OnOrderUpdate) carries no commission — NinjaTrader
            // reports commission per execution, so it arrives with the entry fill event.
            var trackedEntry = TestDataFactory.Order(name: "Entry_t1", instrument: instrument, side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var entryExec = TestDataFactory.Order(name: "Entry_t1", instrument: instrument, side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000, commission: 2.50);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20020);
            var stopFilled = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2, avgFill: 19990, commission: 1.75);

            _orderTracker.TrackEntry("t1", trackedEntry);
            _orderTracker.TrackStopLoss("t1", stop);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);
            _orderExecutionService.CreateStopLossOrder(instrument, account, OrderSide.Sell, 2, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 2, 20020, "t1").Returns(target);
            _pnlCalculator.Calculate(Arg.Any<BrokerOrder>(), Arg.Any<BrokerOrder>()).Returns(new PnlResult(-20, 4.25));

            service.OnExecutionUpdate(entryExec, 20000, 2);
            service.OnExecutionUpdate(stopFilled, 19990, 2);

            _pnlCalculator.Received(1).Calculate(
                Arg.Is<BrokerOrder>(o => o.Name == "Entry_t1" && o.Commission == 2.50),
                Arg.Is<BrokerOrder>(o => o.Name == "Stop_t1" && o.Commission == 1.75));
            _network.Received(1).SendExitFill("t1", 19990, "SL", account: "Sim101", realizedPnl: -20, commission: 4.25, accountBalance: account.CashValue);
        }

        [Fact]
        public void OnExecutionUpdate_MarksClosePending_WhenStopLossPartiallyFills()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.PartFilled, filled: 1);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnExecutionUpdate(stop, 19990, 1);

            _orderTracker.IsClosePending("t1").Should().BeTrue();
        }

        [Fact]
        public void OnExecutionUpdate_MarksClosePending_WhenTakeProfitPartiallyFills()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.PartFilled, filled: 1);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackTakeProfit("t1", target);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnExecutionUpdate(target, 20040, 1);

            _orderTracker.IsClosePending("t1").Should().BeTrue();
        }

        [Fact]
        public void OnOrderUpdate_SuppressesTargetRejection_WhenStopLossTriggered()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Rejected);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _orderTracker.TrackTakeProfit("t1", target);
            _orderTracker.MarkClosePending("t1");

            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _tradeIdExtractor.IsStopOrder("Target_t1").Returns(false);

            service.OnOrderUpdate(target);

            _network.DidNotReceive().SendError("ninjatrader", "order_state", Arg.Any<string>());
            _logger.Infos.Should().Contain(m => m.Contains("Suppressed expected bracket error"));
        }

        [Fact]
        public void OnExecutionUpdate_HandlesCloseFill()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000);
            var close = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackCloseOrder("t1", close);
            _pnlCalculator.Calculate(entry, close).Returns(new PnlResult(10, 0));
            _tradeIdExtractor.ExtractTradeId("Close_t1").Returns("t1");
            _tradeIdExtractor.IsCloseOrder("Close_t1").Returns(true);

            service.OnExecutionUpdate(close, 20005, 2);

            _network.Received(1).SendExitFill("t1", 20005, "CLOSE", account: "Sim101", realizedPnl: 10, commission: 0);
            _orderTracker.TryGetEntry("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnExecutionUpdate_DetectsManualClose()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000, instrument: instrument);
            var manualClose = TestDataFactory.Order(name: "ManualClose", side: OrderSide.Sell, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);

            _orderTracker.TrackEntry("t1", entry);
            _pnlCalculator.Calculate(entry, manualClose).Returns(new PnlResult(5, 0));
            _tradeIdExtractor.ExtractTradeId("ManualClose").Returns("");
            _tradeIdExtractor.IsCloseOrder("ManualClose").Returns(false);

            service.OnExecutionUpdate(manualClose, 20005, 2);

            _network.Received(1).SendExitFill("t1", 20005, "CLOSE", account: "Sim101", realizedPnl: 5, commission: 0);
        }

        [Fact]
        public void OnExecutionUpdate_HandlesShortEntryFill_AndCreatesBracket()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.SellShort, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.BuyToCover, state: OrderState.Working, stopPrice: 20010);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.BuyToCover, state: OrderState.Working, limitPrice: 19980);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("short", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.BuyToCover, 2, 20010, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.BuyToCover, 2, 19980, "t1").Returns(target);

            service.OnExecutionUpdate(entry, 20000, 2);

            _orderExecutionService.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(list => list.Count == 2 && list.Contains(stop) && list.Contains(target)));
            _network.Received(1).SendEntryFill("t1", 20000, 20010, 19980, account: "Sim101", quantity: 2, accountBalance: account.CashValue);
        }

        [Fact]
        public void OnExecutionUpdate_CreatesBracket_WhenEntryPartiallyFilled()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1, quantity: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 1, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 1, 20020, "t1").Returns(target);

            service.OnExecutionUpdate(entry, 20000, 1);

            _orderExecutionService.Received(1).CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 1, 19990, "t1");
            _orderExecutionService.Received(1).CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 1, 20020, "t1");
            _orderExecutionService.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(list => list.Count == 2 && list.Contains(stop) && list.Contains(target)));
            _network.Received(1).SendEntryFill("t1", 20000, 19990, 20020, account: "Sim101", quantity: 1, accountBalance: account.CashValue);
        }

        [Fact]
        public void OnExecutionUpdate_EntryFill_SendsAccountBalance()
        {
            var service = CreateService();
            var account = TestDataFactory.Account(cashValue: 12345.67);
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 2, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 2, 20020, "t1").Returns(target);

            service.OnExecutionUpdate(entry, 20000, 2);

            _network.Received(1).SendEntryFill("t1", 20000, 19990, 20020, account: "Sim101", quantity: 2, accountBalance: 12345.67);
        }

        [Fact]
        public void RunSafetyCheckOnce_Flattens_WhenStopLossMissing()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1").Returns(closeOrder);

            service.RunSafetyCheckOnce();

            _orderExecutionService.Received(1).CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1");
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
            _network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Is<string>(s => s.Contains("t1")));
        }

        [Fact]
        public void RunSafetyCheckOnce_DoesNothing_WhenStopLossWorking()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { stop });
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_DoesNothing_WhenEntryNotFilled()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Working, filled: 0, instrument: instrument, avgFill: 20000);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
        }

        [Fact]
        public void SafetyLoop_FlattensUnprotectedPosition_AfterInterval()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            service.SafetyCheckIntervalMs = 50;
            var flattened = new ManualResetEventSlim(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "t1").Returns(closeOrder);
            _orderExecutionService.When(x => x.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, Arg.Any<OrderSide>(), Arg.Any<int>(), "t1"))
                .Do(x => flattened.Set());

            service.Connect();
            try
            {
                flattened.Wait(TimeSpan.FromMilliseconds(500)).Should().BeTrue("safety guard should flatten within timeout");
                _orderExecutionService.Received(1).SubmitOrder(closeOrder);
            }
            finally
            {
                service.Disconnect("cleanup");
            }
        }

        [Fact]
        public void RunSafetyCheckOnce_FlattensOrphanPosition_WithoutStopLoss()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var closeOrder = TestDataFactory.Order(name: "Close_orphan", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 5, "long", 20100)
            });
            _orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 5, Arg.Is<string>(s => s.StartsWith("orphan_"))).Returns(closeOrder);

            service.RunSafetyCheckOnce();

            _orderExecutionService.Received(1).CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 5, Arg.Is<string>(s => s.StartsWith("orphan_")));
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
            _network.Received(1).SendError("ninjatrader", "missing_stop_loss_guard", Arg.Is<string>(s => s.Contains("Orphan")));
        }

        [Fact]
        public void RunSafetyCheckOnce_DoesNotFlattenOrphanPosition_WhenStopLossWorking()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stop = TestDataFactory.Order(name: "Stop_manual", side: OrderSide.Sell, orderType: OrderType.StopMarket, state: OrderState.Working, quantity: 5, stopPrice: 19900);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { stop });
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 5, "long", 20100)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_RespectsEntryFillGracePeriod()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, orderType: OrderType.StopMarket, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, orderType: OrderType.Limit, state: OrderState.Working, limitPrice: 20020);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 2, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 2, 20020, "t1").Returns(target);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            // Simulate entry fill - this starts the grace period
            service.OnExecutionUpdate(entry, 20000, 2);

            // Immediately run safety check - bracket not yet working, but grace applies
            service.RunSafetyCheckOnce();
            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);

            // Advance past the grace period
            _clock.UtcNow.Returns(_clock.UtcNow.AddSeconds(3));
            service.RunSafetyCheckOnce();

            // Now it should have flattened
            _orderExecutionService.Received(1).CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1");
        }

        [Fact]
        public void RunSafetyCheckOnce_RemovesStaleTrackerEntry_WhenPositionFlat()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            // Position is flat - entry was closed but tracker was not cleaned up.
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>());
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.RunSafetyCheckOnce();
            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
            _orderTracker.GetActiveTradeIds().Should().NotContain("t1");
        }

        [Fact]
        public void OnExecutionUpdate_FlattensPosition_WhenClosePendingEntryFills()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _orderTracker.MarkClosePending("t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _orderExecutionService.CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1").Returns(closeOrder);

            service.OnExecutionUpdate(entry, 20000, 2);

            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
            _orderTracker.TryGetEntry("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void Connect_SendsErrorAndDisconnects_WhenNetworkStartThrows()
        {
            var ex = new InvalidOperationException("network failure");
            _network.When(x => x.Start()).Do(x => throw ex);
            var service = CreateService();

            service.Connect();

            service.IsConnected.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "connection_failed", Arg.Any<string>(), Arg.Any<string>());
            _network.Received(1).Stop();
        }

        [Fact]
        public async Task TestConnectionAsync_ReturnsFalse_WhenPingThrows()
        {
            _network.SendTestPingWithResponse(Arg.Any<double>()).Returns(x => throw new InvalidOperationException("ping failed"));
            var service = CreateService();

            var result = await service.TestConnectionAsync();

            result.Should().BeFalse();
            _logger.Errors.Should().Contain(e => e.Exception.Message == "ping failed");
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenStopOrderMissingTradeId()
        {
            var service = CreateService();
            var stop = TestDataFactory.Order(name: "Stop_", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Stop_").Returns("");
            _tradeIdExtractor.IsStopOrder("Stop_").Returns(true);

            service.OnOrderUpdate(stop);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenTargetOrderMissingTradeId()
        {
            var service = CreateService();
            var target = TestDataFactory.Order(name: "Target_", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Target_").Returns("");
            _tradeIdExtractor.IsTargetOrder("Target_").Returns(true);

            service.OnOrderUpdate(target);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenCloseOrderMissingTradeId()
        {
            var service = CreateService();
            var close = TestDataFactory.Order(name: "Close_", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Close_").Returns("");
            _tradeIdExtractor.IsCloseOrder("Close_").Returns(true);

            service.OnOrderUpdate(close);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnOrderUpdate_RemovesTrade_WhenEntryCancelledAndClosePending()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Cancelled);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.MarkClosePending("t1");
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.OnOrderUpdate(entry);

            _orderTracker.TryGetEntry("t1", out _).Should().BeFalse();
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenEntryCancelledUnexpectedly()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Cancelled);
            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.OnOrderUpdate(entry);

            _network.Received(1).SendError("ninjatrader", "order_state", Arg.Any<string>());
        }

        [Fact]
        public void OnExecutionUpdate_HandlesEntryFill_WithPythonRecovery()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working, stopPrice: 19990);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _network.QueryPositions(Arg.Any<double>()).Returns(new JArray {
                new JObject { ["trade_id"] = "t1", ["direction"] = "long", ["entry_price"] = 20000, ["stop_loss"] = 19990, ["take_profit"] = 20040 }
            });
            _orderExecutionService.CreateStopLossOrder(entry.Instrument, account, OrderSide.Sell, 2, 19990, "t1").Returns(stop);
            _orderExecutionService.CreateTakeProfitOrder(entry.Instrument, account, OrderSide.Sell, 2, 20040, "t1").Returns(target);

            service.OnExecutionUpdate(entry, 20000, 2);

            _orderExecutionService.Received(1).SubmitOrders(Arg.Is<IReadOnlyList<BrokerOrder>>(list => list.Count == 2 && list.Contains(stop) && list.Contains(target)));
            _network.Received(1).SendEntryFill("t1", 20000, 19990, 20040, account: "Sim101", quantity: 2, accountBalance: account.CashValue);
        }

        [Fact]
        public void OnExecutionUpdate_HandlesEntryFill_WhenPythonRecoveryFails()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);

            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _network.QueryPositions(Arg.Any<double>()).Returns((JArray)null);

            service.OnExecutionUpdate(entry, 20000, 2);

            _network.Received(1).SendError("ninjatrader", "fill_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnExecutionUpdate_SendsError_WhenStopLossFillNotTracked()
        {
            var service = CreateService();
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnExecutionUpdate(stop, 19990, 2);

            _network.Received(1).SendError("ninjatrader", "fill_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnExecutionUpdate_SendsError_WhenTakeProfitFillNotTracked()
        {
            var service = CreateService();
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnExecutionUpdate(target, 20040, 2);

            _network.Received(1).SendError("ninjatrader", "fill_tracking_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnOrderUpdate_DiscardsPendingModify_WhenCancelledUnexpectedly()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Cancelled, stopPrice: 19990);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:sl", new PendingModifyInfo(19980, instrument, OrderSide.Sell, 2));
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stop);

            _orderTracker.TryGetPendingModify("t1:sl", out _).Should().BeFalse();
            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateStopLossOrder(null, null, default, 0, 0, null);
        }

        [Fact]
        public void OnOrderUpdate_DiscardsPendingModify_WhenTradeNoLongerActive()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Cancelled, stopPrice: 19990);

            _orderTracker.TrackPendingModify("t1:sl", new PendingModifyInfo(19980, instrument, OrderSide.Sell, 2));
            _orderTracker.ExpectCancellation("Stop_t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stop);

            _orderTracker.TryGetPendingModify("t1:sl", out _).Should().BeFalse();
        }

        [Fact]
        public void OnOrderUpdate_LogsError_WhenReplacementAccountNotFound()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Cancelled, stopPrice: 19990);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:sl", new PendingModifyInfo(19980, instrument, OrderSide.Sell, 2));
            _orderTracker.ExpectCancellation("Stop_t1");
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stop);

            _logger.Errors.Should().Contain(e => e.Message.Contains("account not found"));
            _orderTracker.TryGetPendingModify("t1:sl", out _).Should().BeFalse();
        }

        [Fact]
        public void CommandLoop_SendsDuplicateAck_WhenSameSeqNumReceivedTwice()
        {
            var service = CreateService();
            var envelope1 = MessageEnvelope.Create(MessageType.OrderOpen, TestDataFactory.OrderOpenPayload(), seqNum: 42);
            var envelope2 = MessageEnvelope.Create(MessageType.OrderOpen, TestDataFactory.OrderOpenPayload(), seqNum: 42);
            var callCount = 0;
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x =>
            {
                callCount++;
                if (callCount == 1) return envelope1;
                if (callCount == 2) return envelope2;
                return null;
            });

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _network.Received(1).SendCommandAck(MessageType.OrderOpen, 42, true, Arg.Any<string>(), message: "duplicate");
        }

        [Fact]
        public void CommandLoop_SendsSuccessAck_WhenHandlerReturnsTrue()
        {
            var handler = Substitute.For<ICommandHandler>();
            handler.CommandType.Returns(MessageType.Subscribe);
            handler.Handle(Arg.Any<JObject>()).Returns(true);
            _dispatcher.Register(handler);

            var service = CreateService();
            var envelope = MessageEnvelope.Create(MessageType.Subscribe, TestDataFactory.SubscribePayload(), seqNum: 7);
            var callCount = 0;
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x =>
            {
                callCount++;
                return callCount == 1 ? envelope : null;
            });

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _network.Received(1).SendCommandAck(MessageType.Subscribe, 7, true, null, Arg.Any<string>());
        }

        [Fact]
        public void CommandLoop_SendsFailureAck_WhenHandlerReturnsFalse()
        {
            var handler = Substitute.For<ICommandHandler>();
            handler.CommandType.Returns(MessageType.Subscribe);
            handler.Handle(Arg.Any<JObject>()).Returns(false);
            _dispatcher.Register(handler);

            var service = CreateService();
            var envelope = MessageEnvelope.Create(MessageType.Subscribe, TestDataFactory.SubscribePayload(), seqNum: 8);
            var callCount = 0;
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x =>
            {
                callCount++;
                return callCount == 1 ? envelope : null;
            });

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _network.Received(1).SendCommandAck(MessageType.Subscribe, 8, false, Arg.Any<string>(), message: "handler returned failure");
        }

        [Fact]
        public void ReportPositionsToPython_IncludesUntrackedOrders()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, avgFill: 20000, instrument: instrument);
            var untracked = TestDataFactory.Order(name: "UntrackedStop", side: OrderSide.Sell, state: OrderState.Working, instrument: instrument);

            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder> { untracked });
            _tradeIdExtractor.ExtractTradeId("UntrackedStop").Returns("unknown");

            service.Connect();

            _network.Received(1).SendPositionSync(Arg.Is<JArray>(a => a.Count == 1), Arg.Is<JArray>(a => a.Count == 1));

            service.Disconnect("cleanup");
        }

        [Fact]
        public void ReportPositionsToPython_LogsError_WhenAccountProviderThrows()
        {
            var service = CreateService();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account() });
            service.Connect();

            _accountProvider.GetAccounts().Returns(x => throw new InvalidOperationException("broker down"));
            var method = typeof(ConnectorService).GetMethod("ReportPositionsToPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, null);

            _logger.Errors.Should().Contain(e => e.Message.Contains("[Sync] Error reporting positions"));
            service.Disconnect("cleanup");
        }

        [Fact]
        public void ReportPositionsToPython_DoesNotSendSync_WhenNoPositionsOrUntrackedOrders()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());

            service.Connect();

            _network.DidNotReceiveWithAnyArgs().SendPositionSync(null, null);
            service.Disconnect("cleanup");
        }

        [Fact]
        public void CommandLoop_LogsError_WhenReceiveCommandThrows()
        {
            var service = CreateService();
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x => throw new InvalidOperationException("network error"));

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _logger.Errors.Should().Contain(e => e.Message.Contains("Command loop error"));
        }

        [Fact]
        public void HeartbeatLoop_LogsError_WhenSendHeartbeatThrows()
        {
            var service = CreateService();
            _network.When(x => x.SendHeartbeat("ninjatrader", "ok")).Do(x => throw new InvalidOperationException("heartbeat failed"));

            service.Connect();
            System.Threading.Thread.Sleep(800);
            service.Disconnect("cleanup");

            _logger.Warnings.Should().Contain(w => w.Contains("Heartbeat error"));
        }

        [Fact]
        public void OnOrderUpdate_IgnoresNullOrder()
        {
            var service = CreateService();
            service.OnOrderUpdate(null);
            _logger.Warnings.Should().Contain(w => w.Contains("no associated order"));
        }

        [Fact]
        public void OnOrderUpdate_LogsError_WhenExtractorThrows()
        {
            var service = CreateService();
            var order = TestDataFactory.Order(name: "Entry_t1");
            _tradeIdExtractor.When(x => x.ExtractTradeId("Entry_t1")).Do(x => throw new InvalidOperationException("extractor failed"));

            service.OnOrderUpdate(order);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Order update error"));
        }

        [Fact]
        public void OnOrderUpdate_TracksStopLoss_WhenExtractedTradeIdIsEmpty()
        {
            // Coverage for the stop-order missing tradeId branch already exists; this ensures target branch too.
            var service = CreateService();
            var target = TestDataFactory.Order(name: "Target_", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Target_").Returns("");
            _tradeIdExtractor.IsTargetOrder("Target_").Returns(true);

            service.OnOrderUpdate(target);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Is<string>(s => s.Contains("Target order") && s.Contains("missing trade_id")));
        }

        [Fact]
        public void HandleCancelledBracketOrder_CreatesReplacementTargetOrder()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Cancelled, limitPrice: 20040);
            var newTarget = TestDataFactory.Order(name: "Target_t1_v2", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20050);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:tp", new PendingModifyInfo(20050, instrument, OrderSide.Sell, 2, isTarget: true));
            _orderTracker.ExpectCancellation("Target_t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _orderExecutionService.CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 2, 20050, "t1").Returns(newTarget);

            service.OnOrderUpdate(target);

            _orderExecutionService.Received(1).SubmitOrder(newTarget);
            _orderTracker.TryGetTakeProfit("t1", out var tracked).Should().BeTrue();
            tracked.LimitPrice.Should().Be(20050);
        }

        [Fact]
        public void HandleCancelledBracketOrder_LogsError_WhenTargetReplacementReturnsNull()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Cancelled, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:tp", new PendingModifyInfo(20050, instrument, OrderSide.Sell, 2, isTarget: true));
            _orderTracker.ExpectCancellation("Target_t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _orderExecutionService.CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 2, 20050, "t1").Returns((BrokerOrder)null);

            service.OnOrderUpdate(target);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Failed to create replacement order for t1"));
        }

        [Fact]
        public void IsDuplicateCommand_IgnoresNonPositiveSeqNums()
        {
            var service = CreateService();
            var handler = Substitute.For<ICommandHandler>();
            handler.CommandType.Returns(MessageType.Subscribe);
            handler.Handle(Arg.Any<JObject>()).Returns(true);
            _dispatcher.Register(handler);

            var envelope = MessageEnvelope.Create(MessageType.Subscribe, TestDataFactory.SubscribePayload(), seqNum: 0);
            var callCount = 0;
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x =>
            {
                callCount++;
                return callCount == 1 ? envelope : null;
            });

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            handler.Received(1).Handle(Arg.Any<JObject>());
        }

        [Fact]
        public void Disconnect_JoinsCommandAndHeartbeatThreads_WhenAlive()
        {
            var service = CreateService();
            service.Connect();
            System.Threading.Thread.Sleep(100);

            service.Disconnect("cleanup");

            service.IsConnected.Should().BeFalse();
            _network.Received(1).Stop();
        }

        [Fact]
        public void CommandLoop_SendsDispatcherUnavailableAck_WhenDispatcherIsNull()
        {
            var service = CreateService();
            var envelope = MessageEnvelope.Create(MessageType.Subscribe, TestDataFactory.SubscribePayload(), seqNum: 1);
            _network.ReceiveCommand(Arg.Any<int>()).Returns(envelope, (MessageEnvelope)null);

            var field = typeof(ConnectorService).GetField("_dispatcher", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            field.SetValue(service, null);

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _network.Received(1).SendCommandAck("subscribe", 1, false, null, "dispatcher not available");
        }

        [Fact]
        public void CommandLoop_LogsError_WhenErrorAckThrows()
        {
            var service = CreateService();
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x => throw new InvalidOperationException("network error"));
            _network.WhenForAnyArgs(x => x.SendCommandAck(null, 0, false, null, Arg.Any<string>())).Do(x => throw new InvalidOperationException("ack failed"));

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _logger.Errors.Should().Contain(e => e.Message.Contains("Failed to send error ack"));
        }

        [Fact]
        public void IsDuplicateCommand_EvictsOldSeqNums_WhenLimitExceeded()
        {
            var service = CreateService();
            var processedField = typeof(ConnectorService).GetField("_processedSeqNums", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var queueField = typeof(ConnectorService).GetField("_processedSeqNumQueue", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var processed = (System.Collections.Generic.HashSet<int>)processedField.GetValue(service);
            var queue = (System.Collections.Generic.Queue<int>)queueField.GetValue(service);
            for (int i = 1; i <= 1001; i++)
            {
                processed.Add(i);
                queue.Enqueue(i);
            }

            var method = typeof(ConnectorService).GetMethod("IsDuplicateCommand", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var result = method.Invoke(service, new object[] { 9999 });

            result.Should().Be(false);
            processed.Count.Should().BeLessOrEqualTo(1000);
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenCloseOrderNameMissingTradeId()
        {
            var service = CreateService();
            var closeOrder = TestDataFactory.Order(name: "Close_", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Close_").Returns("");
            _tradeIdExtractor.IsCloseOrder("Close_").Returns(true);

            service.OnOrderUpdate(closeOrder);

            _network.Received(1).SendError("ninjatrader", "order_tracking_failed", Arg.Is<string>(s => s.Contains("Close order") && s.Contains("missing trade_id")));
        }

        [Fact]
        public void OnOrderUpdate_IgnoresCancelledBracketOrder_WhenNoPendingModify()
        {
            var service = CreateService();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Cancelled);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stopOrder);

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateStopLossOrder(null, null, OrderSide.Buy, 0, 0, null);
        }

        [Theory]
        [InlineData(OrderState.Rejected)]
        [InlineData(OrderState.Cancelled)]
        public void OnOrderUpdate_SuppressesBracketError_WhenTradeIsClosePending(OrderState state)
        {
            var service = CreateService();
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: state);
            _orderTracker.MarkClosePending("t1");
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnOrderUpdate(targetOrder);

            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
            _logger.Infos.Should().Contain(i => i.Contains("Suppressed expected bracket error"));
        }

        [Theory]
        [InlineData(OrderState.Rejected)]
        [InlineData(OrderState.Cancelled)]
        public void OnOrderUpdate_SuppressesBracketError_WhenEntryNoLongerTracked(OrderState state)
        {
            var service = CreateService();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: state);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stopOrder);

            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
            _logger.Infos.Should().Contain(i => i.Contains("Suppressed expected bracket error"));
        }

        [Theory]
        [InlineData(OrderState.Rejected)]
        [InlineData(OrderState.Cancelled)]
        public void OnOrderUpdate_SuppressesBracketError_WhenEntryNotFilled(OrderState state)
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Working);
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: state);
            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnOrderUpdate(targetOrder);

            _network.DidNotReceiveWithAnyArgs().SendError(null, null, null);
            _logger.Infos.Should().Contain(i => i.Contains("Suppressed expected bracket error"));
        }

        [Fact]
        public void OnOrderUpdate_SendsError_WhenBracketOrderRejectedUnexpectedly()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Rejected);
            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnOrderUpdate(stopOrder);

            _network.Received(1).SendError("ninjatrader", "order_state", Arg.Is<string>(s => s.Contains("Stop_t1") && s.Contains("Rejected")));
        }

        [Fact]
        public void HandleCancelledBracketOrder_LogsError_WhenCreateTakeProfitOrderThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Cancelled, limitPrice: 20040);

            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackPendingModify("t1:tp", new PendingModifyInfo(20050, instrument, OrderSide.Sell, 2, isTarget: true));
            _orderTracker.ExpectCancellation("Target_t1");
            _accountProvider.GetAccount("Sim101").Returns(account);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _orderExecutionService.When(x => x.CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 2, 20050, "t1")).Do(x => throw new InvalidOperationException("create failed"));

            service.OnOrderUpdate(target);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Error creating replacement order"));
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void OnExecutionUpdate_IgnoresNullOrder()
        {
            var service = CreateService();
            service.OnExecutionUpdate(null, 20000, 1);
            _logger.Warnings.Should().Contain(w => w.Contains("Execution update with no associated order"));
        }

        [Fact]
        public void OnExecutionUpdate_LogsError_WhenExtractorThrows()
        {
            var service = CreateService();
            var order = TestDataFactory.Order(name: "Entry_t1");
            _tradeIdExtractor.When(x => x.ExtractTradeId("Entry_t1")).Do(x => throw new InvalidOperationException("extractor failed"));

            service.OnExecutionUpdate(order, 20000, 1);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Execution update error"));
        }

        [Fact]
        public void HandleEntryFill_LogsError_WhenAccountNotFound()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var order = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 20, 2.0));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _accountProvider.GetAccount("Sim101").Returns((BrokerAccount)null);

            service.OnExecutionUpdate(order, 20000, 2);

            _network.Received(1).SendError("ninjatrader", "fill_tracking_failed", Arg.Is<string>(s => s.Contains("account not found")));
        }

        [Fact]
        public void HandleEntryFill_FlattenPosition_WhenStopOrderIsNull()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var order = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working, limitPrice: 20040);
            var flatOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 20, 2.0));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.CreateStopLossOrder(instrument, account, OrderSide.Sell, 2, 19980, "t1").Returns((BrokerOrder)null);
            _orderExecutionService.CreateTakeProfitOrder(instrument, account, OrderSide.Sell, 2, 20040, "t1").Returns(target);
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, "t1").Returns(flatOrder);

            service.OnExecutionUpdate(order, 20000, 2);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Failed to create complete bracket"));
            _network.Received(1).SendError("ninjatrader", "bracket_creation_failed", Arg.Any<string>());
            _orderExecutionService.Received(1).SubmitOrder(flatOrder);
        }

        [Fact]
        public void HandleEntryFill_FlattenPosition_WhenBracketCreationThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var order = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var flatOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 20, 2.0));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.When(x => x.CreateStopLossOrder(instrument, account, OrderSide.Sell, 2, 19980, "t1")).Do(x => throw new InvalidOperationException("create failed"));
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, "t1").Returns(flatOrder);

            service.OnExecutionUpdate(order, 20000, 2);

            _logger.Errors.Should().Contain(e => e.Message.Contains("Failed to create bracket orders"));
            _network.Received(1).SendError("ninjatrader", "bracket_creation_failed", Arg.Any<string>());
            _orderExecutionService.Received(1).SubmitOrder(flatOrder);
        }

        [Fact]
        public void OnOrderUpdate_FlattensPartialFill_WhenEntryCancelled()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Cancelled, filled: 1, quantity: 2, instrument: instrument);
            var flatOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 20, 2.0));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 1, "t1").Returns(flatOrder);

            service.OnOrderUpdate(entryOrder);

            _logger.Warnings.Should().Contain(w => w.Contains("cancelled after partial fill"));
            _network.Received(1).SendError("ninjatrader", "partial_fill_cancelled", Arg.Any<string>());
            _orderExecutionService.Received(1).SubmitOrder(flatOrder);
        }

        [Fact]
        public void TryRecoverPendingEntryFromPython_ReturnsNull_WhenPositionsEmpty()
        {
            var service = CreateService();
            _network.QueryPositions(Arg.Any<double>()).Returns(new JArray());

            var method = typeof(ConnectorService).GetMethod("TryRecoverPendingEntryFromPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var result = method.Invoke(service, new object[] { "t1" });

            result.Should().BeNull();
        }

        [Fact]
        public void TryRecoverPendingEntryFromPython_ReturnsNull_WhenTradeIdNotFoundInPositions()
        {
            var service = CreateService();
            _network.QueryPositions(Arg.Any<double>()).Returns(new JArray {
                new JObject { ["trade_id"] = "other", ["direction"] = "long", ["entry_price"] = 20000, ["stop_loss"] = 19980, ["take_profit"] = 20040 }
            });

            var method = typeof(ConnectorService).GetMethod("TryRecoverPendingEntryFromPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var result = method.Invoke(service, new object[] { "t1" });

            result.Should().BeNull();
            _logger.Warnings.Should().Contain(w => w.Contains("Trade t1 not found in Python positions"));
        }

        [Fact]
        public void TryRecoverPendingEntryFromPython_ReturnsNull_WhenPositionDataIncomplete()
        {
            var service = CreateService();
            _network.QueryPositions(Arg.Any<double>()).Returns(new JArray {
                new JObject { ["trade_id"] = "t1", ["direction"] = "long", ["entry_price"] = 0, ["stop_loss"] = 0 }
            });

            var method = typeof(ConnectorService).GetMethod("TryRecoverPendingEntryFromPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var result = method.Invoke(service, new object[] { "t1" });

            result.Should().BeNull();
            _logger.Warnings.Should().Contain(w => w.Contains("incomplete data"));
        }

        [Fact]
        public void TryRecoverPendingEntryFromPython_ReturnsNull_WhenQueryThrows()
        {
            var service = CreateService();
            _network.QueryPositions(Arg.Any<double>()).Returns(x => throw new InvalidOperationException("query failed"));

            var method = typeof(ConnectorService).GetMethod("TryRecoverPendingEntryFromPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            var result = method.Invoke(service, new object[] { "t1" });

            result.Should().BeNull();
            _logger.Errors.Should().Contain(e => e.Message.Contains("[Recovery] Failed to query Python"));
        }

        [Fact]
        public void ReportPositionsToPython_SkipsActiveTrade_WhenEntryNotTracked()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackStopLoss("t1", stopOrder);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());

            var method = typeof(ConnectorService).GetMethod("ReportPositionsToPython", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, null);

            _network.DidNotReceiveWithAnyArgs().SendPositionSync(null, null);
        }

        [Fact]
        public void OnExecutionUpdate_StopLossPartialFill_WarnsAndReturns()
        {
            var service = CreateService();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.PartFilled, filled: 1);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.OnExecutionUpdate(stopOrder, 19990, 1);

            _logger.Warnings.Should().Contain(w => w.Contains("Stop Stop_t1 state=PartFilled"));
            _network.DidNotReceiveWithAnyArgs().SendExitFill(null, 0, null);
        }

        [Fact]
        public void OnExecutionUpdate_TakeProfitPartialFill_WarnsAndReturns()
        {
            var service = CreateService();
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.PartFilled, filled: 1);
            _orderTracker.TrackTakeProfit("t1", targetOrder);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);

            service.OnExecutionUpdate(targetOrder, 20040, 1);

            _logger.Warnings.Should().Contain(w => w.Contains("Target Target_t1 state=PartFilled"));
            _network.DidNotReceiveWithAnyArgs().SendExitFill(null, 0, null);
        }

        [Fact]
        public void OnExecutionUpdate_ClosePartialFill_WarnsAndReturns()
        {
            var service = CreateService();
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.PartFilled, filled: 1);
            _orderTracker.TrackCloseOrder("t1", closeOrder);
            _tradeIdExtractor.ExtractTradeId("Close_t1").Returns("t1");
            _tradeIdExtractor.IsCloseOrder("Close_t1").Returns(true);

            service.OnExecutionUpdate(closeOrder, 20000, 1);

            _logger.Warnings.Should().Contain(w => w.Contains("Close Close_t1 state=PartFilled"));
            _network.DidNotReceiveWithAnyArgs().SendExitFill(null, 0, null);
        }

        [Fact]
        public void OnExecutionUpdate_StopLossFill_LogsNa_WhenPnlCalculatorReturnsNull()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);
            _pnlCalculator.Calculate(entry, stopOrder).Returns((PnlResult)null);

            service.OnExecutionUpdate(stopOrder, 19990, 2);

            _network.Received(1).SendExitFill("t1", 19990, "SL", account: stopOrder.AccountName, realizedPnl: Arg.Is<double?>(x => x == null));
        }

        [Fact]
        public void OnExecutionUpdate_DetectsManualClose_ForSellShortEntry()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.SellShort, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "ManualClose", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("ManualClose").Returns("");
            _tradeIdExtractor.IsEntryOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsStopOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsTargetOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsCloseOrder("ManualClose").Returns(false);

            service.OnExecutionUpdate(closeOrder, 20000, 2);

            _network.Received(1).SendExitFill("t1", 20000, "CLOSE", account: closeOrder.AccountName, realizedPnl: Arg.Any<double?>());
        }

        [Fact]
        public void OnExecutionUpdate_DoesNotDetectManualClose_WhenSidesAreNotOpposing()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "ManualClose", side: OrderSide.BuyToCover, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            _orderTracker.TrackEntry("t1", entry);
            _tradeIdExtractor.ExtractTradeId("ManualClose").Returns("");
            _tradeIdExtractor.IsEntryOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsStopOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsTargetOrder("ManualClose").Returns(false);
            _tradeIdExtractor.IsCloseOrder("ManualClose").Returns(false);

            service.OnExecutionUpdate(closeOrder, 20000, 2);

            _network.DidNotReceiveWithAnyArgs().SendExitFill(null, 0, null);
        }

        [Fact]
        public void CalculateSlTp_Throws_WhenDirectionInvalid()
        {
            var service = CreateService();
            var method = typeof(ConnectorService).GetMethod("CalculateSlTp", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            Action act = () => method.Invoke(service, new object[] { 20000, "sideways", 20, 2.0 });
            act.Should().Throw<TargetInvocationException>().WithInnerException<ArgumentException>();
        }

        [Fact]
        public void CancelWorkingBracketOrders_Returns_WhenAccountNotFound()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _accountProvider.GetAccount(entry.AccountName).Returns((BrokerAccount)null);

            var method = typeof(ConnectorService).GetMethod("CancelWorkingBracketOrders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, new object[] { "t1", entry.AccountName });

            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(null);
        }

        [Fact]
        public void CancelWorkingBracketOrders_SkipsStop_WhenStopOrderNotWorking()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _orderTracker.TrackTakeProfit("t1", targetOrder);
            _accountProvider.GetAccount(account.Name).Returns(account);

            var method = typeof(ConnectorService).GetMethod("CancelWorkingBracketOrders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, new object[] { "t1", account.Name });

            _orderExecutionService.DidNotReceive().CancelOrder(stopOrder);
            _orderExecutionService.Received(1).CancelOrder(targetOrder);
        }

        [Fact]
        public void FormatExceptionDetails_IncludesInnerException_WhenPresent()
        {
            var inner = new InvalidOperationException("inner error");
            var ex = new InvalidOperationException("outer error", inner);
            var method = typeof(ConnectorService).GetMethod("FormatExceptionDetails", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            var result = method.Invoke(null, new object[] { ex }) as string;

            result.Should().Contain("InnerException: InvalidOperationException");
            result.Should().Contain("InnerMessage: inner error");
        }

        [Fact]
        public void Pair_ReturnsEmpty_WhenCurrentInstrumentIsNull()
        {
            var service = CreateService();
            _streamingCoordinator.CurrentInstrument.Returns((string)null);
            service.Pair.Should().BeEmpty();
        }

        [Fact]
        public void OnExecutionUpdate_SendsError_WhenCloseFillNotTracked()
        {
            var service = CreateService();
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _tradeIdExtractor.ExtractTradeId("Close_t1").Returns("t1");
            _tradeIdExtractor.IsCloseOrder("Close_t1").Returns(true);

            service.OnExecutionUpdate(closeOrder, 20000, 2);

            _network.Received(1).SendError("ninjatrader", "fill_tracking_failed", Arg.Is<string>(s => s.Contains("Close fill")));
        }

        [Fact]
        public void OnExecutionUpdate_TakeProfitFill_LogsNa_WhenPnlCalculatorReturnsNull()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var target = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Filled, filled: 2);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackTakeProfit("t1", target);
            _tradeIdExtractor.ExtractTradeId("Target_t1").Returns("t1");
            _tradeIdExtractor.IsTargetOrder("Target_t1").Returns(true);
            _pnlCalculator.Calculate(entry, target).Returns((PnlResult)null);

            service.OnExecutionUpdate(target, 20040, 2);

            _network.Received(1).SendExitFill("t1", 20040, "TP", account: target.AccountName, realizedPnl: Arg.Is<double?>(x => x == null));
        }

        [Fact]
        public void CancelWorkingBracketOrders_LogsWarning_WhenStopCancelThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _accountProvider.GetAccount(account.Name).Returns(account);
            _orderExecutionService.When(x => x.CancelOrder(stopOrder)).Do(x => throw new InvalidOperationException("cancel failed"));

            var method = typeof(ConnectorService).GetMethod("CancelWorkingBracketOrders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, new object[] { "t1", account.Name });

            _logger.Warnings.Should().Contain(w => w.Contains("Failed to cancel stop order"));
        }

        [Fact]
        public void CancelWorkingBracketOrders_LogsWarning_WhenTargetCancelThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackTakeProfit("t1", targetOrder);
            _accountProvider.GetAccount(account.Name).Returns(account);
            _orderExecutionService.When(x => x.CancelOrder(targetOrder)).Do(x => throw new InvalidOperationException("cancel failed"));

            var method = typeof(ConnectorService).GetMethod("CancelWorkingBracketOrders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, new object[] { "t1", account.Name });

            _logger.Warnings.Should().Contain(w => w.Contains("Failed to cancel target order"));
        }

        [Fact]
        public void CancelWorkingBracketOrders_CancelsWorkingOrders_AndLogsInfo()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var stopOrder = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            var targetOrder = TestDataFactory.Order(name: "Target_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackStopLoss("t1", stopOrder);
            _orderTracker.TrackTakeProfit("t1", targetOrder);
            _accountProvider.GetAccount(account.Name).Returns(account);

            var method = typeof(ConnectorService).GetMethod("CancelWorkingBracketOrders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            method.Invoke(service, new object[] { "t1", account.Name });

            _orderExecutionService.Received(1).CancelOrder(stopOrder);
            _orderExecutionService.Received(1).CancelOrder(targetOrder);
            _logger.Infos.Should().Contain(i => i.Contains("Cancelling stop for t1") && i.Contains("tracked=Working") && i.Contains("live=Working"));
            _logger.Infos.Should().Contain(i => i.Contains("Cancelling target for t1") && i.Contains("tracked=Working") && i.Contains("live=Working"));
        }

        [Fact]
        public void OnOrderUpdate_TracksCloseOrder_WhenTradeIdPresent()
        {
            var service = CreateService();
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);
            _tradeIdExtractor.ExtractTradeId("Close_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Close_t1").Returns(false);
            _tradeIdExtractor.IsStopOrder("Close_t1").Returns(false);
            _tradeIdExtractor.IsTargetOrder("Close_t1").Returns(false);
            _tradeIdExtractor.IsCloseOrder("Close_t1").Returns(true);

            service.OnOrderUpdate(closeOrder);

            _orderTracker.TryGetCloseOrder("t1", out var tracked).Should().BeTrue();
            tracked.Should().Be(closeOrder);
            _logger.Infos.Should().Contain(i => i.Contains("TRACKING close order for t1"));
        }

        [Fact]
        public void Disconnect_JoinsHeartbeatAndSafetyThreads_WhenAlive()
        {
            var service = CreateService();
            service.SafetyCheckIntervalMs = 50;
            service.Connect();
            System.Threading.Thread.Sleep(800);

            service.Disconnect("cleanup");

            service.IsConnected.Should().BeFalse();
            _network.Received(1).Stop();
        }

        [Fact]
        public void CommandLoop_LogsWarning_WhenTradeIdExtractionThrows()
        {
            var service = CreateService();
            var payload = new JObject { ["trade_id"] = new ThrowingToStringJObject() };
            var envelope = MessageEnvelope.Create(MessageType.OrderOpen, payload, seqNum: 1);

            var callCount = 0;
            _network.ReceiveCommand(Arg.Any<int>()).Returns(x =>
            {
                callCount++;
                return callCount == 1 ? envelope : null;
            });

            service.Connect();
            System.Threading.Thread.Sleep(300);
            service.Disconnect("cleanup");

            _logger.Warnings.Should().Contain(w => w.Contains("Failed to extract trade_id"));
        }

        [Fact]
        public void SafetyLoop_LogsError_WhenRunSafetyCheckThrows()
        {
            var orderTracker = Substitute.For<IOrderTracker>();
            orderTracker.GetActiveTradeIds().Returns(x => throw new InvalidOperationException("tracker down"));
            var service = new ConnectorService(
                _config, _network, _logger, _dispatcher, orderTracker,
                _streamingCoordinator, _accountProvider, _orderExecutionService,
                _instrumentProvider, _barHistoryService, _pnlCalculator, _clock, _tradeIdExtractor);
            service.SafetyGuardEnabled = true;
            service.SafetyCheckIntervalMs = 50;

            service.Connect();
            System.Threading.Thread.Sleep(200);
            service.Disconnect("cleanup");

            _logger.Errors.Should().Contain(e => e.Message.Contains("Safety guard error"));
        }

        [Fact]
        public void RunSafetyCheckOnce_LogsError_WhenGetAccountPositionsThrows()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAccountPositions(account).Returns(x => throw new InvalidOperationException("positions down"));

            service.RunSafetyCheckOnce();

            _logger.Errors.Should().Contain(e => e.Message.Contains("failed to read positions"));
        }

        [Fact]
        public void RunSafetyCheckOnce_IgnoresActiveTradeIdWithoutEntry()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackStopLoss("t1", stop);

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_IgnoresCancelledEntry()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Cancelled, filled: 1, instrument: instrument);
            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 1, "long", 20000)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_IgnoresClosePendingEntry()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.MarkClosePending("t1");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_TrustsTrackedStop_WhenAccountLookupFails()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument);
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackStopLoss("t1", stop);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns((BrokerAccount)null);
            _tradeIdExtractor.ExtractTradeId("Stop_t1").Returns("t1");
            _tradeIdExtractor.IsStopOrder("Stop_t1").Returns(true);

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_Flattens_WhenStopLossMissing_WithoutPendingEntry()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument, avgFill: 20000);
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });
            _orderExecutionService.CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1").Returns(closeOrder);

            service.RunSafetyCheckOnce();

            _orderExecutionService.Received(1).CreateMarketCloseOrder(entry.Instrument, account, OrderSide.Sell, 2, "t1");
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
        }

        [Fact]
        public void RunSafetyCheckOnce_SkipsOrphanPosition_WithZeroQuantity()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 0, "long", 20000)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_SkipsOrphanPosition_WhenPairMismatch()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var esInstrument = TestDataFactory.Instrument(name: "ES 09-25", master: "ES");
            _streamingCoordinator.CurrentInstrument.Returns("MNQ 09-25");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, esInstrument, 2, "long", 4500)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_SkipsOrphanPosition_WhenAccountMissing()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account(name: "Sim101") });
            _orderExecutionService.GetAccountPositions(Arg.Any<BrokerAccount>()).Returns(new List<BrokerPosition>
            {
                new BrokerPosition("OtherAccount", instrument, 2, "long", 20000)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void RunSafetyCheckOnce_SkipsOrphanPosition_WhenTrackedEntryExists()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Working, filled: 0, instrument: instrument);
            _orderTracker.TrackEntry("t1", entry);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 2, "long", 20000)
            });

            service.RunSafetyCheckOnce();

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void ShouldSuppressBracketOrderError_ReturnsFalse_WhenTradeIdIsEmpty()
        {
            var service = CreateService();
            var target = TestDataFactory.Order(name: "Target_", side: OrderSide.Sell, state: OrderState.Rejected);
            _tradeIdExtractor.ExtractTradeId("Target_").Returns("");
            _tradeIdExtractor.IsTargetOrder("Target_").Returns(true);

            service.OnOrderUpdate(target);

            _network.Received(1).SendError("ninjatrader", "order_state", Arg.Is<string>(s => s.Contains("Target_") && s.Contains("Rejected")));
        }

        [Fact]
        public void OnExecutionUpdate_EntryFill_NoFills_SkipsBracket()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Working, filled: 0);
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);

            service.OnExecutionUpdate(entry, 20000, 0);

            _logger.Infos.Should().Contain(i => i.Contains("no fills yet"));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateStopLossOrder(null, null, default, 0, 0, null);
        }

        [Fact]
        public void OnExecutionUpdate_EntryFill_NoInstrument_LogsError()
        {
            var service = CreateService();
            var entry = new BrokerOrder("Entry_t1", "Sim101", null, OrderType.Market, OrderSide.Buy, OrderState.Filled, 2, 2, 20000);
            _orderTracker.TrackPendingEntry("t1", new PendingEntryInfo("long", 10, 2));
            _tradeIdExtractor.ExtractTradeId("Entry_t1").Returns("t1");
            _tradeIdExtractor.IsEntryOrder("Entry_t1").Returns(true);
            _accountProvider.GetAccount("Sim101").Returns(TestDataFactory.Account());

            service.OnExecutionUpdate(entry, 20000, 2);

            _network.Received(1).SendError("ninjatrader", "bracket_creation_failed", Arg.Is<string>(s => s.Contains("instrument is null")));
        }

        [Fact]
        public void FlattenPosition_Returns_WhenEntryHasNoFills()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 0);
            var method = typeof(ConnectorService).GetMethod("FlattenPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { entry, new PendingEntryInfo("long", 10, 2), "t1", "test" });

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void FlattenPosition_Returns_WhenCloseOrderAlreadyTracked()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument);
            var closeOrder = TestDataFactory.Order(name: "Close_t1", side: OrderSide.Sell, state: OrderState.Working);
            _orderTracker.TrackEntry("t1", entry);
            _orderTracker.TrackCloseOrder("t1", closeOrder);
            _accountProvider.GetAccount("Sim101").Returns(account);
            var method = typeof(ConnectorService).GetMethod("FlattenPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { entry, new PendingEntryInfo("long", 10, 2), "t1", "test" });

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void FlattenPosition_LogsError_WhenAccountOrInstrumentMissing()
        {
            var service = CreateService();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: null);
            var method = typeof(ConnectorService).GetMethod("FlattenPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { entry, new PendingEntryInfo("long", 10, 2), "t1", "test" });

            _logger.Errors.Should().Contain(e => e.Message.Contains("Cannot flatten t1"));
            _network.Received(1).SendError("ninjatrader", "flatten_failed", Arg.Any<string>());
        }

        [Fact]
        public void FlattenPosition_LogsError_WhenCreateMarketCloseOrderThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entry = TestDataFactory.Order(name: "Entry_t1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2, instrument: instrument);
            _accountProvider.GetAccount("Sim101").Returns(account);
            _orderExecutionService.When(x => x.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, "t1"))
                .Do(x => throw new InvalidOperationException("create failed"));
            var method = typeof(ConnectorService).GetMethod("FlattenPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { entry, new PendingEntryInfo("long", 10, 2), "t1", "test" });

            _logger.Errors.Should().Contain(e => e.Message.Contains("Exception flattening t1"));
            _network.Received(1).SendError("ninjatrader", "flatten_failed", Arg.Is<string>(s => s.Contains("create failed")));
        }

        [Fact]
        public void HasWorkingStopForPosition_ReturnsFalse_ForNonWorkingOrMismatchedOrders()
        {
            var service = CreateService();
            var instrument = TestDataFactory.Instrument();
            var otherInstrument = TestDataFactory.Instrument(name: "ES 09-25", master: "ES");
            var position = new BrokerPosition("Sim101", instrument, 2, "long", 20000);
            var nonWorkingStop = TestDataFactory.Order(name: "Stop", side: OrderSide.Sell, orderType: OrderType.StopMarket, state: OrderState.Filled, instrument: instrument);
            var wrongInstrumentStop = TestDataFactory.Order(name: "Stop2", side: OrderSide.Sell, orderType: OrderType.StopMarket, state: OrderState.Working, instrument: otherInstrument);
            var wrongSideStop = TestDataFactory.Order(name: "Stop3", side: OrderSide.Buy, orderType: OrderType.StopMarket, state: OrderState.Working, instrument: instrument);
            var method = typeof(ConnectorService).GetMethod("HasWorkingStopForPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { position, new List<BrokerOrder> { nonWorkingStop, wrongInstrumentStop, wrongSideStop } })
                .Should().Be(false);
        }

        [Fact]
        public void ShouldSkipOrphanFlatten_Skips_WhenRecentlyFlattened()
        {
            var service = CreateService();
            var field = typeof(ConnectorService).GetField("_recentOrphanFlattens", BindingFlags.NonPublic | BindingFlags.Instance);
            var dict = (System.Collections.Generic.Dictionary<string, DateTime>)field.GetValue(service);
            dict["Sim101|MNQ"] = DateTime.UtcNow;
            var method = typeof(ConnectorService).GetMethod("ShouldSkipOrphanFlatten", BindingFlags.NonPublic | BindingFlags.Instance);

            var result = method.Invoke(service, new object[] { "Sim101", "MNQ" });

            result.Should().Be(true);
        }

        [Fact]
        public void FlattenAccountPosition_Returns_WhenRecentlyFlattened()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var field = typeof(ConnectorService).GetField("_recentOrphanFlattens", BindingFlags.NonPublic | BindingFlags.Instance);
            var dict = (System.Collections.Generic.Dictionary<string, DateTime>)field.GetValue(service);
            dict[$"{account.Name}|{instrument.MasterInstrumentName}"] = DateTime.UtcNow;
            var method = typeof(ConnectorService).GetMethod("FlattenAccountPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { account, new BrokerPosition(account.Name, instrument, 2, "long", 20000), "test" });

            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateMarketCloseOrder(null, null, default, 0, null);
        }

        [Fact]
        public void FlattenAccountPosition_LogsError_WhenCreateMarketCloseOrderReturnsNull()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, Arg.Is<string>(s => s.StartsWith("orphan_"))).Returns((BrokerOrder)null);
            var method = typeof(ConnectorService).GetMethod("FlattenAccountPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { account, new BrokerPosition(account.Name, instrument, 2, "long", 20000), "test" });

            _logger.Errors.Should().Contain(e => e.Message.Contains("Could not create orphan flatten order"));
            _network.Received(1).SendError("ninjatrader", "flatten_failed", Arg.Any<string>());
        }

        [Fact]
        public void FlattenAccountPosition_LogsError_WhenCreateMarketCloseOrderThrows()
        {
            var service = CreateService();
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _orderExecutionService.When(x => x.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, Arg.Is<string>(s => s.StartsWith("orphan_"))))
                .Do(x => throw new InvalidOperationException("create failed"));
            var method = typeof(ConnectorService).GetMethod("FlattenAccountPosition", BindingFlags.NonPublic | BindingFlags.Instance);

            method.Invoke(service, new object[] { account, new BrokerPosition(account.Name, instrument, 2, "long", 20000), "test" });

            _logger.Errors.Should().Contain(e => e.Message.Contains("Exception flattening orphan position"));
            _network.Received(1).SendError("ninjatrader", "flatten_failed", Arg.Any<string>());
        }

        [Fact]
        public void RunSafetyCheckOnce_HitsTrackedEntryLambda_WhenNoEntryTracked()
        {
            var service = CreateService();
            service.SafetyGuardEnabled = true;
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stop = TestDataFactory.Order(name: "Stop_t1", side: OrderSide.Sell, state: OrderState.Working);
            var closeOrder = TestDataFactory.Order(name: "Close_orphan", side: OrderSide.Sell, state: OrderState.Working);

            _orderTracker.TrackStopLoss("t1", stop);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderExecutionService.GetWorkingOrders(account).Returns(new List<BrokerOrder>());
            _orderExecutionService.GetAccountPositions(account).Returns(new List<BrokerPosition>
            {
                new BrokerPosition(account.Name, instrument, 3, "long", 20100)
            });
            _orderExecutionService.CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 3, Arg.Is<string>(s => s.StartsWith("orphan_"))).Returns(closeOrder);

            service.RunSafetyCheckOnce();

            _orderExecutionService.Received(1).CreateMarketCloseOrder(Arg.Any<BrokerInstrument>(), account, OrderSide.Sell, 3, Arg.Is<string>(s => s.StartsWith("orphan_")));
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
        }

        [Fact]
        public void ShouldSkipOrphanFlatten_CleansExpiredEntries()
        {
            var service = CreateService();
            var field = typeof(ConnectorService).GetField("_recentOrphanFlattens", BindingFlags.NonPublic | BindingFlags.Instance);
            var dict = (System.Collections.Generic.Dictionary<string, DateTime>)field.GetValue(service);
            dict["Sim101|MNQ"] = DateTime.UtcNow;
            dict["Sim101|ES"] = DateTime.UtcNow.AddSeconds(-60); // expired
            var method = typeof(ConnectorService).GetMethod("ShouldSkipOrphanFlatten", BindingFlags.NonPublic | BindingFlags.Instance);

            var result = method.Invoke(service, new object[] { "Sim101", "MNQ" });

            result.Should().Be(true);
            dict.ContainsKey("Sim101|ES").Should().BeFalse();
        }

        [Fact]
        public void GetStats_UsesDefaults_WhenStreamingCoordinatorIsNull()
        {
            var service = CreateService();
            var field = typeof(ConnectorService).GetField("_streamingCoordinator", BindingFlags.NonPublic | BindingFlags.Instance);
            field.SetValue(service, null);

            var stats = service.GetStats();

            stats.Should().Contain("Ticks: 0");
            stats.Should().Contain("Bars: 0");
            stats.Should().Contain("Partial: 0");
        }

        private class ThrowingToStringJObject : JObject
        {
            public override string ToString() => throw new InvalidOperationException("ToString fails");
        }
    }
}
