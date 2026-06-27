using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class TickEventArgsTests
    {
        [Fact]
        public void Constructor_Should_Assign_All_Properties()
        {
            var time = new DateTime(2025, 6, 27, 12, 0, 0, DateTimeKind.Utc);
            var args = new TickEventArgs("MNQ 09-25", 20000.5, 100, time, bid: 20000.25, ask: 20000.75);

            args.Instrument.Should().Be("MNQ 09-25");
            args.Price.Should().Be(20000.5);
            args.Volume.Should().Be(100);
            args.Time.Should().Be(time);
            args.Bid.Should().Be(20000.25);
            args.Ask.Should().Be(20000.75);
        }

        [Fact]
        public void Constructor_With_Null_Instrument_Should_Throw()
        {
            Action act = () => new TickEventArgs(null, 20000.5, 100, DateTime.UtcNow);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("instrument");
        }
    }
}
