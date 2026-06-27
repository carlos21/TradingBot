using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class PendingEntryInfoTests
    {
        [Fact]
        public void Constructor_Should_Assign_Properties()
        {
            var info = new PendingEntryInfo("long", 10, 2);

            info.Direction.Should().Be("long");
            info.SlPoints.Should().Be(10);
            info.RrRatio.Should().Be(2);
        }

        [Fact]
        public void Constructor_With_Null_Direction_Should_Throw()
        {
            Action act = () => new PendingEntryInfo(null, 10, 2);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("direction");
        }
    }
}
