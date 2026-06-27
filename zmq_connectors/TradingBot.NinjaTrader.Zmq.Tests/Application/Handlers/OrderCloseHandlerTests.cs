using System;
using System.Collections.Generic;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application.Handlers;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application.Handlers
{
    public class OrderCloseHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradeIdExtractor _tradeIdExtractor;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly OrderCloseHandler _handler;

        public OrderCloseHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _orderTracker = Substitute.For<IOrderTracker>();
            _tradeIdExtractor = Substitute.For<ITradeIdExtractor>();
            _tradingMode = Substitute.For<ITradingMode>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _orderExecutionService = Substitute.For<IOrderExecutionService>();
            _handler = new OrderCloseHandler(_network, _logger, _orderTracker, _tradeIdExtractor, _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "orderTracker")]
        [InlineData(3, "tradeIdExtractor")]
        [InlineData(4, "tradingMode")]
        [InlineData(5, "accountProvider")]
        [InlineData(6, "instrumentProvider")]
        [InlineData(7, "orderExecutionService")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var orderTracker = nullIndex == 2 ? null : _orderTracker;
            var tradeIdExtractor = nullIndex == 3 ? null : _tradeIdExtractor;
            var tradingMode = nullIndex == 4 ? null : _tradingMode;
            var accountProvider = nullIndex == 5 ? null : _accountProvider;
            var instrumentProvider = nullIndex == 6 ? null : _instrumentProvider;
            var orderExecutionService = nullIndex == 7 ? null : _orderExecutionService;

            Action act = () => new OrderCloseHandler(network, logger, orderTracker, tradeIdExtractor, tradingMode, accountProvider, instrumentProvider, orderExecutionService);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_OrderClose()
        {
            _handler.CommandType.Should().Be(MessageType.OrderClose);
        }

        [Fact]
        public void Handle_SimulationMode_SendsExitFill()
        {
            _tradingMode.IsSimulation.Returns(true);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _network.Received(1).SendExitFill("test-1", 0, "CLOSE", account: (string)null);
            _orderExecutionService.DidNotReceiveWithAnyArgs().FindOrderByName(null, null);
        }

        [Fact]
        public void Handle_LiveMode_CancelsWorkingOrders()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", state: OrderState.Working);
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", state: OrderState.Working);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderExecutionService.Received(1).CancelOrder(entryOrder);
            _orderExecutionService.Received(1).CancelOrder(stopOrder);
            _orderExecutionService.Received(1).CancelOrder(targetOrder);
            _orderTracker.Received(2).ExpectCancellation(Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_SubmitsCloseOrder_WhenEntryFilled()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy, state: OrderState.Filled, filled: 2);
            var closeOrder = TestDataFactory.Order(name: "Close_test-1", side: OrderSide.Sell);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, "test-1").Returns(closeOrder);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
            _orderTracker.Received(1).TrackCloseOrder("test-1", closeOrder);
        }

        [Fact]
        public void Handle_LiveMode_MarksClosePending_WhenOrdersCancelledButNotFilled()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderTracker.Received(1).MarkClosePending("test-1");
        }

        [Fact]
        public void Handle_LiveMode_WarnsAndIgnores_WhenTradeNotTracked()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, Arg.Any<string>()).Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderTracker.DidNotReceive().RemoveTrade("test-1");
            _logger.Warnings.Should().Contain(w => w.Contains("Trade not tracked"));
        }

        [Fact]
        public void Handle_LiveMode_RemovesTrade_WhenTrackedButNoBrokerOrders()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderTracker.Received(1).RemoveTrade("test-1");
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount>());

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountHasNoConnection()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account(hasConnection: false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenMultipleAccountsAndNoneSpecified()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account1 = TestDataFactory.Account(name: "Sim101");
            var account2 = TestDataFactory.Account(name: "Sim102");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account1, account2 });

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Is<string>(s => s.Contains("No account available")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountNameNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account(name: "Sim101") });

            var result = _handler.Handle(TestDataFactory.OrderClosePayload(account: "Sim999"));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Is<string>(s => s.Contains("No account available")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentNotFoundForClose()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", state: OrderState.Filled, filled: 2);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns((BrokerInstrument)null);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Is<string>(s => s.Contains("Instrument")));
        }

        [Fact]
        public void Handle_LiveMode_LogsError_WhenCreateMarketCloseOrderReturnsNull()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", state: OrderState.Filled, filled: 2);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 2, "test-1").Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _logger.Errors.Should().Contain(e => e.Message.Contains("CreateOrder returned NULL"));
        }

        [Fact]
        public void Handle_LiveMode_SubmitsCloseOrder_WhenEntryPartFilled()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy, state: OrderState.PartFilled, filled: 1);
            var closeOrder = TestDataFactory.Order(name: "Close_test-1", side: OrderSide.Sell);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(true);
            _orderExecutionService.FindOrderByName(account, "Entry_test-1").Returns(entryOrder);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderExecutionService.CreateMarketCloseOrder(instrument, account, OrderSide.Sell, 1, "test-1").Returns(closeOrder);

            var result = _handler.Handle(TestDataFactory.OrderClosePayload());

            result.Should().BeTrue();
            _orderExecutionService.Received(1).SubmitOrder(closeOrder);
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenTradeIdMissing()
        {
            _tradingMode.IsSimulation.Returns(false);

            var result = _handler.Handle(new JObject { ["instrument"] = "MNQ 09-25" });

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentMissing()
        {
            _tradingMode.IsSimulation.Returns(false);

            var result = _handler.Handle(new JObject { ["trade_id"] = "test-1" });

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_close_failed", Arg.Any<string>());
        }
    }
}
