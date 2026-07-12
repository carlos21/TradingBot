using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class BrokerPositionTests
    {
        private readonly BrokerInstrument _instrument = TestDataFactory.Instrument();

        [Fact]
        public void Constructor_SetsProperties()
        {
            var position = new BrokerPosition("Sim101", _instrument, 5, "long", 20000);

            position.AccountName.Should().Be("Sim101");
            position.Instrument.Should().Be(_instrument);
            position.Quantity.Should().Be(5);
            position.Direction.Should().Be("long");
            position.AveragePrice.Should().Be(20000);
            position.IsLong.Should().BeTrue();
            position.IsShort.Should().BeFalse();
        }

        [Fact]
        public void Constructor_ShortDirection_ReportsIsShort()
        {
            var position = new BrokerPosition("Sim101", _instrument, 3, "short", 20100);

            position.IsLong.Should().BeFalse();
            position.IsShort.Should().BeTrue();
        }

        [Theory]
        [InlineData("accountName")]
        [InlineData("instrument")]
        [InlineData("direction")]
        public void Constructor_Throws_WhenRequiredArgumentIsNull(string nullArg)
        {
            var accountName = nullArg == "accountName" ? null : "Sim101";
            var instrument = nullArg == "instrument" ? null : _instrument;
            var direction = nullArg == "direction" ? null : "long";

            Action act = () => new BrokerPosition(accountName, instrument, 1, direction, 20000);

            act.Should().Throw<ArgumentNullException>();
        }

        [Fact]
        public void AveragePrice_Getter_ReturnsValue()
        {
            var position = new BrokerPosition("Sim101", _instrument, 1, "long", 12345.67);

            position.AveragePrice.Should().Be(12345.67);
        }
    }
}
