using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class BrokerInstrumentTests
    {
        [Fact]
        public void Constructor_With_All_Properties_Should_Assign_Them()
        {
            var instrument = new BrokerInstrument("MNQ 09-25", "MNQ", 0.5);

            instrument.Name.Should().Be("MNQ 09-25");
            instrument.MasterInstrumentName.Should().Be("MNQ");
            instrument.PointValue.Should().Be(0.5);
        }

        [Fact]
        public void Constructor_With_Null_MasterInstrumentName_Should_Default_To_Name()
        {
            var instrument = new BrokerInstrument("ES 12-25", null, 12.5);

            instrument.MasterInstrumentName.Should().Be("ES 12-25");
        }

        [Fact]
        public void Constructor_With_Null_Name_Should_Throw()
        {
            Action act = () => new BrokerInstrument(null, "MNQ", 0.5);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("name");
        }
    }
}
