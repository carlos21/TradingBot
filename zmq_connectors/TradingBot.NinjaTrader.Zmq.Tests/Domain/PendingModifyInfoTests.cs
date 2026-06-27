using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class PendingModifyInfoTests
    {
        [Fact]
        public void Constructor_Should_Assign_All_Properties()
        {
            var instrument = TestDataFactory.Instrument();
            var info = new PendingModifyInfo(19980, instrument, OrderSide.Sell, 2, isTarget: true);

            info.NewPrice.Should().Be(19980);
            info.Instrument.Should().Be(instrument);
            info.OrderSide.Should().Be(OrderSide.Sell);
            info.Quantity.Should().Be(2);
            info.IsTarget.Should().BeTrue();
            info.CreatedAt.Should().BeCloseTo(DateTime.UtcNow, TimeSpan.FromSeconds(1));
        }

        [Fact]
        public void Constructor_With_Null_Instrument_Should_Throw()
        {
            Action act = () => new PendingModifyInfo(19980, null, OrderSide.Sell, 2);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("instrument");
        }
    }
}
