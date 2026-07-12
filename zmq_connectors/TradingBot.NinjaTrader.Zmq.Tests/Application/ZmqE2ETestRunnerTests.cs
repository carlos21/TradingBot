using System.Collections.Generic;
using System.Threading.Tasks;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using NSubstitute;
using TradingBot.NinjaTrader.Zmq.Application;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Application
{
    public class ZmqE2ETestRunnerTests
    {
        private readonly TestLogger _logger;
        private readonly IZmqNetwork _network;
        private readonly IAccountProvider _accountProvider;
        private readonly ITradingMode _tradingMode;
        private readonly ZmqE2ETestRunner _runner;

        public ZmqE2ETestRunnerTests()
        {
            _logger = new TestLogger();
            _network = Substitute.For<IZmqNetwork>();
            _accountProvider = Substitute.For<IAccountProvider>();
            _tradingMode = Substitute.For<ITradingMode>();
            _runner = new ZmqE2ETestRunner(_network, _logger, _accountProvider, _tradingMode);
        }

        [Theory]
        [InlineData(0, "network")]
        [InlineData(1, "logger")]
        [InlineData(2, "accountProvider")]
        [InlineData(3, "tradingMode")]
        public void Constructor_Throws_WhenDependencyIsNull(int nullIndex, string paramName)
        {
            var network = nullIndex == 0 ? null : _network;
            var logger = nullIndex == 1 ? null : _logger;
            var accountProvider = nullIndex == 2 ? null : _accountProvider;
            var tradingMode = nullIndex == 3 ? null : _tradingMode;

            System.Action act = () => new ZmqE2ETestRunner(network, logger, accountProvider, tradingMode);
            act.Should().Throw<System.ArgumentNullException>().Which.ParamName.Should().Be(paramName);
        }

        [Fact]
        public async Task RunAllScenariosAsync_Aborts_WhenNoAccounts()
        {
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount>());

            var result = await _runner.RunAllScenariosAsync();

            result.Should().Be(0);
            _logger.Errors.Should().Contain(e => e.Message.Contains("ABORTED"));
        }

        [Fact]
        public async Task RunAllScenariosAsync_Aborts_WhenLiveAccount()
        {
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account(name: "Live101") });

            var result = await _runner.RunAllScenariosAsync();

            result.Should().Be(0);
            _logger.Errors.Should().Contain(e => e.Message.Contains("SAFETY BLOCK"));
        }

        [Fact]
        public async Task RunAllScenariosAsync_RunsBasicAndFeatureScenarios()
        {
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount>
            {
                TestDataFactory.Account(name: "Sim101"),
                TestDataFactory.Account(name: "Sim102")
            });

            var result = await _runner.RunAllScenariosAsync();

            result.Should().Be(8); // 3 basic + 5 feature
            _network.Received(3).SendTestStart(
                Arg.Is<string>(s => s == "tp_hit" || s == "sl_hit" || s == "session_end"),
                entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0, accounts: (JArray)null);
            _network.Received(1).SendTestStart("multi_account", entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0, accounts: Arg.Any<JArray>());
        }

        [Fact]
        public async Task RunAllScenariosAsync_HandlesNetworkException()
        {
            _accountProvider.GetAccounts().Returns(new List<BrokerAccount> { TestDataFactory.Account(name: "Sim101") });
            _network.WhenForAnyArgs(x => x.SendTestStart(null, 0, 0, 0, null)).Do(x => throw new System.InvalidOperationException("network down"));

            var result = await _runner.RunAllScenariosAsync();

            result.Should().Be(0);
            _logger.Errors.Should().Contain(e => e.Exception != null && e.Exception.Message == "network down");
        }
        [Fact]
        public async Task RunAllScenariosAsync_SkipsMultiAccount_WhenNoAccountsConnectedAtScenarioTime()
        {
            // First call validates the simulation environment, second call inside multi_account returns empty.
            _accountProvider.GetAccounts().Returns(
                new List<BrokerAccount> { TestDataFactory.Account(name: "Sim101") },
                new List<BrokerAccount>());

            var result = await _runner.RunAllScenariosAsync();

            result.Should().Be(7); // 3 basic + 4 feature (multi_account skipped)
            _logger.Warnings.Should().Contain(w => w.Contains("multi_account: No accounts connected"));
        }
    }
}
