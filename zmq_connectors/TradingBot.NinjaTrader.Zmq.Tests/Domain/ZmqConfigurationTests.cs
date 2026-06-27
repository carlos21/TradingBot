using System;
using System.IO;
using FluentAssertions;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class ZmqConfigurationTests
    {
        [Fact]
        public void Default_Constructor_Should_Apply_Defaults_And_Derive_Addresses()
        {
            var config = new ZmqConfiguration();

            config.Host.Should().Be("127.0.0.1");
            config.MarketPort.Should().Be(5555);
            config.CommandPort.Should().Be(5556);
            config.QueryPort.Should().Be(5557);
            config.HeartbeatPort.Should().Be(5558);
            config.HistoryDays.Should().Be(30);
            config.BatchSize.Should().Be(500);
            config.MaxTicksPerSecond.Should().Be(10);
            config.PlatformVersion.Should().Be("2.0.0");
            config.AutoConnectOnStartup.Should().BeFalse();
            config.AutoShowWindow.Should().BeTrue();
            config.EnableFileLogging.Should().BeTrue();

            config.MarketDataAddress.Should().Be("tcp://127.0.0.1:5555");
            config.CommandAddress.Should().Be("tcp://127.0.0.1:5556");
            config.QueryAddress.Should().Be("tcp://127.0.0.1:5557");
            config.HeartbeatAddress.Should().Be("tcp://127.0.0.1:5558");
        }

        [Fact]
        public void Constructor_Should_Use_Provided_LogDirectory()
        {
            var config = new ZmqConfiguration(logDirectory: "C:\\logs");
            config.LogDirectory.Should().Be("C:\\logs");
        }

        [Fact]
        public void Constructor_With_Null_Host_Should_Throw()
        {
            Action act = () => new ZmqConfiguration(host: null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("host");
        }

        [Fact]
        public void Constructor_With_Null_Instrument_Should_Throw()
        {
            Action act = () => new ZmqConfiguration(instrument: null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("instrument");
        }

        [Fact]
        public void Constructor_With_Null_PlatformVersion_Should_Throw()
        {
            Action act = () => new ZmqConfiguration(platformVersion: null);
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("platformVersion");
        }

        [Fact]
        public void WithInstrument_Should_Create_New_Config_With_Different_Instrument()
        {
            var original = new ZmqConfiguration(host: "127.0.0.1", marketPort: 5555, commandPort: 5556, queryPort: 5557, heartbeatPort: 5558);
            var modified = original.WithInstrument("ES 12-25");

            modified.Should().NotBeSameAs(original);
            modified.Instrument.Should().Be("ES 12-25");
            modified.MarketPort.Should().Be(original.MarketPort);
        }

        [Fact]
        public void Default_LogDirectory_Should_Be_NinjaTrader_Logs()
        {
            var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            var expected = Path.Combine(documents, "NinjaTrader 8", "bin", "Custom", "logs");

            var config = new ZmqConfiguration();
            config.LogDirectory.Should().Be(expected);
        }
    }
}
