using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class PnlResultTests
    {
        [Fact]
        public void Constructor_Should_Assign_All_Properties()
        {
            var result = new PnlResult(123.45, 2.5);

            result.RealizedPnl.Should().Be(123.45);
            result.Commission.Should().Be(2.5);
        }
    }
}
