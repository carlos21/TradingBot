using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class BrokerOrderTests
    {
        [Fact]
        public void Constructor_Should_Assign_All_Properties()
        {
            var instrument = TestDataFactory.Instrument();
            var time = new DateTime(2025, 6, 27, 12, 0, 0, DateTimeKind.Utc);
            var order = new BrokerOrder(
                "Entry_abc",
                "Sim101",
                instrument,
                OrderType.Limit,
                OrderSide.Buy,
                OrderState.Working,
                2,
                0,
                averageFillPrice: 0,
                stopPrice: 100,
                limitPrice: 99,
                ocoId: "oco-1",
                time: time);

            order.Name.Should().Be("Entry_abc");
            order.AccountName.Should().Be("Sim101");
            order.Instrument.Should().Be(instrument);
            order.OrderType.Should().Be(OrderType.Limit);
            order.OrderSide.Should().Be(OrderSide.Buy);
            order.OrderState.Should().Be(OrderState.Working);
            order.Quantity.Should().Be(2);
            order.Filled.Should().Be(0);
            order.AverageFillPrice.Should().Be(0);
            order.StopPrice.Should().Be(100);
            order.LimitPrice.Should().Be(99);
            order.OcoId.Should().Be("oco-1");
            order.Time.Should().Be(time);
        }

        [Theory]
        [InlineData(OrderState.Working, true)]
        [InlineData(OrderState.Accepted, true)]
        [InlineData(OrderState.Submitted, true)]
        [InlineData(OrderState.PartFilled, true)]
        [InlineData(OrderState.Filled, false)]
        [InlineData(OrderState.Cancelled, false)]
        [InlineData(OrderState.Rejected, false)]
        [InlineData(OrderState.Initialized, false)]
        public void IsWorking_Should_Be_True_For_Active_States(OrderState state, bool expected)
        {
            var order = TestDataFactory.Order(state: state);
            order.IsWorking.Should().Be(expected);
        }

        [Fact]
        public void WithState_Should_Create_Order_With_New_State_And_Same_Other_Properties()
        {
            var original = TestDataFactory.Order(quantity: 3, stopPrice: 10);
            var modified = original.WithState(OrderState.Filled);

            modified.Should().NotBeSameAs(original);
            modified.OrderState.Should().Be(OrderState.Filled);
            modified.Quantity.Should().Be(original.Quantity);
            modified.StopPrice.Should().Be(original.StopPrice);
        }

        [Fact]
        public void WithFill_Should_Create_Order_With_New_Fill_And_Same_State()
        {
            var original = TestDataFactory.Order(quantity: 3, state: OrderState.PartFilled);
            var modified = original.WithFill(averageFillPrice: 20000, filled: 2);

            modified.Should().NotBeSameAs(original);
            modified.Filled.Should().Be(2);
            modified.AverageFillPrice.Should().Be(20000);
            modified.OrderState.Should().Be(OrderState.PartFilled);
        }
    }
}
