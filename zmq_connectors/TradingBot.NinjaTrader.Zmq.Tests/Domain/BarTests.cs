using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class BarTests
    {
        [Fact]
        public void Constructor_Should_Assign_All_Properties()
        {
            var time = new DateTime(2025, 6, 27, 12, 0, 0, DateTimeKind.Utc);
            var bar = new Bar(time, 20000, 20010, 19990, 20005, 1500);

            bar.Time.Should().Be(time);
            bar.Open.Should().Be(20000);
            bar.High.Should().Be(20010);
            bar.Low.Should().Be(19990);
            bar.Close.Should().Be(20005);
            bar.Volume.Should().Be(1500);
        }
    }
}
