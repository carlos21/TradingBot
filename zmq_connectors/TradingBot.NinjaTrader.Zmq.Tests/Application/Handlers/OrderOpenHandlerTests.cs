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
    public class OrderOpenHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly OrderOpenHandler _handler;

        public OrderOpenHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _orderTracker = Substitute.For<IOrderTracker>();
            _tradingMode = Substitute.For<ITradingMode>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _orderExecutionService = Substitute.For<IOrderExecutionService>();
            _handler = new OrderOpenHandler(_network, _logger, _orderTracker, _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "orderTracker")]
        [InlineData(3, "tradingMode")]
        [InlineData(4, "accountProvider")]
        [InlineData(5, "instrumentProvider")]
        [InlineData(6, "orderExecutionService")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var orderTracker = nullIndex == 2 ? null : _orderTracker;
            var tradingMode = nullIndex == 3 ? null : _tradingMode;
            var accountProvider = nullIndex == 4 ? null : _accountProvider;
            var instrumentProvider = nullIndex == 5 ? null : _instrumentProvider;
            var orderExecutionService = nullIndex == 6 ? null : _orderExecutionService;

            Action act = () => new OrderOpenHandler(network, logger, orderTracker, tradingMode, accountProvider, instrumentProvider, orderExecutionService);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_OrderOpen()
        {
            _handler.CommandType.Should().Be(MessageType.OrderOpen);
        }

        [Fact]
        public void Handle_SimulationMode_SendsEntryFill()
        {
            _tradingMode.IsSimulation.Returns(true);
            var payload = TestDataFactory.OrderOpenPayload(entryPrice: 20000, stopLoss: 19980, takeProfit: 20040, contracts: 5);

            var result = _handler.Handle(payload);

            result.Should().BeTrue();
            _network.Received(1).SendEntryFill("test-1", 20000, 19980, 20040, account: (string)null);
            _network.Received(1).SendTradeLog("test-1", "NT:SIMULATE", Arg.Any<string>());
            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateEntryOrder(null, null, default, 0, null);
        }

        [Fact]
        public void Handle_LiveMode_CreatesAndSubmitsEntryOrder()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument(pointValue: 0.5);
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(false);
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload(riskUsd: 100));

            result.Should().BeTrue();
            _orderExecutionService.Received(1).SubmitOrder(entryOrder);
            _orderTracker.Received(1).TrackEntry("test-1", entryOrder);
            _orderTracker.Received(1).TrackPendingEntry("test-1", Arg.Any<PendingEntryInfo>());
        }

        [Theory]
        [InlineData(100.0, null, 10)]   // risk_usd 100 / (20 pts * 0.5 pv) = 10
        [InlineData(null, 1.0, 50)]     // 1% of 50000 / 10 = 50 (capped at 100)
        [InlineData(null, null, 1)]     // no risk -> 1
        public void Handle_LiveMode_CalculatesPositionSize(double? riskUsd, double? riskPct, int expectedQty)
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account(cashValue: 50000);
            var instrument = TestDataFactory.Instrument(pointValue: 0.5);
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.Buy);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(false);
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);

            _handler.Handle(TestDataFactory.OrderOpenPayload(riskUsd: riskUsd, riskPct: riskPct, riskPoints: 20));

            _orderExecutionService.Received(1).CreateEntryOrder(instrument, account, OrderSide.Buy, expectedQty, "test-1");
        }

        [Fact]
        public void Handle_LiveMode_RejectsDuplicateTradeId()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(true);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload());

            result.Should().BeTrue();
            _orderExecutionService.DidNotReceiveWithAnyArgs().CreateEntryOrder(null, null, default, 0, null);
            _logger.Warnings.Should().Contain(w => w.Contains("Duplicate place_order"));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount>());

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload(account: "Missing"));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountHasNoConnection()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account(hasConnection: false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns((BrokerInstrument)null);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Any<string>());
        }

        [Theory]
        [InlineData("", "long", 20, "trade_id is required")]
        [InlineData("test-1", "invalid", 20, "Invalid direction")]
        [InlineData("test-1", "long", 0, "Invalid sl_points")]
        public void Handle_LiveMode_ReturnsFalse_WhenPayloadInvalid(string tradeId, string direction, double riskPoints, string expectedError)
        {
            _tradingMode.IsSimulation.Returns(false);
            var payload = new JObject
            {
                ["trade_id"] = tradeId,
                ["direction"] = direction,
                ["instrument"] = "MNQ 09-25",
                ["risk_points"] = riskPoints
            };

            var result = _handler.Handle(payload);

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Is<string>(s => s.Contains(expectedError)));
        }

        [Fact]
        public void Handle_LiveMode_CreatesShortEntryOrder()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1", side: OrderSide.SellShort);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(false);
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.SellShort, Arg.Any<int>(), "test-1").Returns(entryOrder);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload(direction: "short"));

            result.Should().BeTrue();
            _orderExecutionService.Received(1).CreateEntryOrder(instrument, account, OrderSide.SellShort, Arg.Any<int>(), "test-1");
        }

        [Fact]
        public void Handle_LiveMode_UsesSpecifiedAccount()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account(name: "Sim202");
            var instrument = TestDataFactory.Instrument();
            var entryOrder = TestDataFactory.Order(name: "Entry_test-1");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(false);
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.CreateEntryOrder(instrument, account, OrderSide.Buy, Arg.Any<int>(), "test-1").Returns(entryOrder);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload(account: "Sim202"));

            result.Should().BeTrue();
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenMultipleAccountsAndNoneSpecified()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account1 = TestDataFactory.Account(name: "Sim101");
            var account2 = TestDataFactory.Account(name: "Sim102");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account1, account2 });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(TestDataFactory.Instrument());

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Is<string>(s => s.Contains("No account available")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentMissing()
        {
            _tradingMode.IsSimulation.Returns(false);
            var payload = TestDataFactory.OrderOpenPayload();
            payload.Remove("instrument");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account() });

            var result = _handler.Handle(payload);

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Is<string>(s => s.Contains("instrument is required")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenCreateEntryOrderReturnsNull()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingEntry("test-1", out Arg.Any<PendingEntryInfo>()).Returns(false);
            _orderTracker.TryGetEntry("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.CreateEntryOrder(null, null, default, 0, null).ReturnsForAnyArgs((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderOpenPayload());

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_open_failed", Arg.Is<string>(s => s.Contains("Failed to create entry order")));
        }
    }
}
