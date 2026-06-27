using System;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class BrokerAccountTests
    {
        [Fact]
        public void Constructor_With_Name_Should_Assign_Name_And_Defaults()
        {
            var account = new BrokerAccount("Sim101");

            account.Name.Should().Be("Sim101");
            account.HasConnection.Should().BeTrue();
            account.CashValue.Should().Be(0);
        }

        [Fact]
        public void Constructor_With_All_Properties_Should_Assign_Them()
        {
            var account = new BrokerAccount("LiveAccount", hasConnection: false, cashValue: 123456.78);

            account.Name.Should().Be("LiveAccount");
            account.HasConnection.Should().BeFalse();
            account.CashValue.Should().Be(123456.78);
        }

        [Fact]
        public void Constructor_With_Null_Name_Should_Throw()
        {
            Action act = () => new BrokerAccount(null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("name");
        }
    }
}
