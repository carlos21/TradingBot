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
    public class OrderModifyHandlerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradeIdExtractor _tradeIdExtractor;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;
        private readonly OrderModifyHandler _handler;

        public OrderModifyHandlerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _orderTracker = Substitute.For<IOrderTracker>();
            _tradeIdExtractor = Substitute.For<ITradeIdExtractor>();
            _tradingMode = Substitute.For<ITradingMode>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _instrumentProvider = Substitute.For<IInstrumentProvider>();
            _orderExecutionService = Substitute.For<IOrderExecutionService>();
            _handler = new OrderModifyHandler(_network, _logger, _orderTracker, _tradeIdExtractor, _tradingMode, _accountProvider, _instrumentProvider, _orderExecutionService);
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

            Action act = () => new OrderModifyHandler(network, logger, orderTracker, tradeIdExtractor, tradingMode, accountProvider, instrumentProvider, orderExecutionService);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public void CommandType_Should_Be_OrderModify()
        {
            _handler.CommandType.Should().Be(MessageType.OrderModify);
        }

        [Fact]
        public void Handle_SimulationMode_SendsTradeLog()
        {
            _tradingMode.IsSimulation.Returns(true);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980, takeProfit: 20040));

            result.Should().BeTrue();
            _network.Received(1).SendTradeLog("test-1", "NT:SIMULATE", Arg.Any<string>());
            _orderExecutionService.DidNotReceiveWithAnyArgs().FindOrderByName(null, null);
        }

        [Fact]
        public void Handle_LiveMode_ModifiesStopLoss()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeTrue();
            _orderTracker.Received(1).TrackPendingModify("test-1:sl", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).RemovePendingModify("test-1:sl");
            _orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19980) < 0.01),
                Arg.Is<double?>(x => x == null));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(null);
            _orderTracker.DidNotReceiveWithAnyArgs().ExpectCancellation(null);
        }

        [Fact]
        public void Handle_LiveMode_ModifiesTakeProfit()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeTrue();
            _orderTracker.Received(1).TrackPendingModify("test-1:tp", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).RemovePendingModify("test-1:tp");
            _orderExecutionService.Received(1).ModifyOrder(
                targetOrder,
                Arg.Is<double?>(x => x == null),
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 20040) < 0.01));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(null);
            _orderTracker.DidNotReceiveWithAnyArgs().ExpectCancellation(null);
        }

        [Fact]
        public void Handle_LiveMode_RejectsDuplicatePendingModify()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(true);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse(); // handler returns false when no valid modifications occurred
            _orderExecutionService.DidNotReceiveWithAnyArgs().FindOrderByName(null, null);
            _logger.Warnings.Should().Contain(w => w.Contains("SL modify already pending"));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenStopOrderNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenTargetOrderNotModifiable()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Filled);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenOrderNotModifiable()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", state: OrderState.Filled);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount>());

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountHasNoConnection()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account(hasConnection: false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenMultipleAccountsAndNoneSpecified()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account1 = TestDataFactory.Account(name: "Sim101");
            var account2 = TestDataFactory.Account(name: "Sim102");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account1, account2 });

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("No account available")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenAccountNameNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account(name: "Sim101") });

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980, account: "Sim999"));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("No account available")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentMissing()
        {
            _tradingMode.IsSimulation.Returns(false);
            var payload = TestDataFactory.OrderModifyPayload(stopLoss: 19980);
            payload.Remove("instrument");
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account() });

            var result = _handler.Handle(payload);

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("instrument is required")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenTradeIdMissing()
        {
            _tradingMode.IsSimulation.Returns(false);
            var payload = TestDataFactory.OrderModifyPayload(stopLoss: 19980);
            payload["trade_id"] = "";
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account() });

            var result = _handler.Handle(payload);

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("trade_id is required")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenPayloadInvalid()
        {
            _tradingMode.IsSimulation.Returns(false);

            var result = _handler.Handle(new JObject { ["trade_id"] = "test-1", ["instrument"] = "MNQ 09-25" });

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Any<string>());
        }

        [Fact]
        public void Handle_LiveMode_ModifiesStopLossAndTakeProfit()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working);
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980, takeProfit: 20040));

            result.Should().BeTrue();
            _orderTracker.Received(1).TrackPendingModify("test-1:sl", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).TrackPendingModify("test-1:tp", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).RemovePendingModify("test-1:sl");
            _orderTracker.Received(1).RemovePendingModify("test-1:tp");
            _orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19980) < 0.01),
                Arg.Is<double?>(x => x == null));
            _orderExecutionService.Received(1).ModifyOrder(
                targetOrder,
                Arg.Is<double?>(x => x == null),
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 20040) < 0.01));
            _orderExecutionService.DidNotReceiveWithAnyArgs().CancelOrder(null);
            _orderTracker.DidNotReceiveWithAnyArgs().ExpectCancellation(null);
        }

        [Fact]
        public void Handle_PurgesStaleModifies_BeforeProcessing()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            _orderTracker.Received(1).PurgeStaleModifies(TimeSpan.FromSeconds(60), _logger);
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenInstrumentNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns((BrokerInstrument)null);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("Instrument 'MNQ 09-25' not found")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenTargetOrderNotFound()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns((BrokerOrder)null);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeFalse();
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("Target order not found")));
        }

        [Fact]
        public void Handle_LiveMode_ReturnsFalse_WhenTakeProfitModifyAlreadyPending()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(true);

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeFalse();
            _logger.Warnings.Should().Contain(w => w.Contains("TP modify already pending"));
            _network.Received(1).SendTradeLog("test-1", "NT:MODIFY", Arg.Is<string>(s => s.Contains("TP modify rejected")));
        }

        [Fact]
        public void Handle_LiveMode_ModifiesTrackedStopLoss()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(x => { x[1] = stopOrder; return true; });

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeTrue();
            _orderExecutionService.DidNotReceiveWithAnyArgs().FindOrderByName(null, null);
            _orderExecutionService.Received(1).ModifyOrder(
                stopOrder,
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 19980) < 0.01),
                Arg.Is<double?>(x => x == null));
        }

        [Fact]
        public void Handle_LiveMode_ModifiesTrackedTakeProfit()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(x => { x[1] = targetOrder; return true; });

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeTrue();
            _orderExecutionService.DidNotReceiveWithAnyArgs().FindOrderByName(null, null);
            _orderExecutionService.Received(1).ModifyOrder(
                targetOrder,
                Arg.Is<double?>(x => x == null),
                Arg.Is<double?>(x => x.HasValue && Math.Abs(x.Value - 20040) < 0.01));
        }

        [Fact]
        public void Handle_LiveMode_RemovesPendingModify_WhenStopModifyThrows()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var stopOrder = TestDataFactory.Order(name: "Stop_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:sl", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetStopLoss("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Stop_test-1").Returns(stopOrder);
            _orderExecutionService
                .When(x => x.ModifyOrder(stopOrder, Arg.Is<double?>(s => s.HasValue && Math.Abs(s.Value - 19980) < 0.01), Arg.Is<double?>(l => l == null)))
                .Do(x => throw new InvalidOperationException("modify failed"));

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(stopLoss: 19980));

            result.Should().BeFalse();
            _orderTracker.Received(1).TrackPendingModify("test-1:sl", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).RemovePendingModify("test-1:sl");
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("modify failed")));
        }

        [Fact]
        public void Handle_LiveMode_RemovesPendingModify_WhenTargetModifyThrows()
        {
            _tradingMode.IsSimulation.Returns(false);
            var account = TestDataFactory.Account();
            var instrument = TestDataFactory.Instrument();
            var targetOrder = TestDataFactory.Order(name: "Target_test-1", side: OrderSide.Sell, state: OrderState.Working);

            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { account });
            _instrumentProvider.GetInstrument("MNQ 09-25").Returns(instrument);
            _orderTracker.TryGetPendingModify("test-1:tp", out Arg.Any<PendingModifyInfo>()).Returns(false);
            _orderTracker.TryGetTakeProfit("test-1", out Arg.Any<BrokerOrder>()).Returns(false);
            _orderExecutionService.FindOrderByName(account, "Target_test-1").Returns(targetOrder);
            _orderExecutionService
                .When(x => x.ModifyOrder(targetOrder, Arg.Is<double?>(s => s == null), Arg.Is<double?>(l => l.HasValue && Math.Abs(l.Value - 20040) < 0.01)))
                .Do(x => throw new InvalidOperationException("modify failed"));

            var result = _handler.Handle(TestDataFactory.OrderModifyPayload(takeProfit: 20040));

            result.Should().BeFalse();
            _orderTracker.Received(1).TrackPendingModify("test-1:tp", Arg.Any<PendingModifyInfo>());
            _orderTracker.Received(1).RemovePendingModify("test-1:tp");
            _network.Received(1).SendError("ninjatrader", "order_modify_failed", Arg.Is<string>(s => s.Contains("modify failed")));
        }
    }
}
