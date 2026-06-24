using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application
{
    /// <summary>
    /// E2E Test Runner - orchestrates test scenarios.
    /// </summary>
    public sealed class ZmqE2ETestRunner
    {
        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IAccountProvider _accountProvider;
        private readonly ITradingMode _tradingMode;

        public ZmqE2ETestRunner(IZmqNetwork network, ILogger logger, IAccountProvider accountProvider, ITradingMode tradingMode)
        {
            _network = network ?? throw new System.ArgumentNullException(nameof(network));
            _logger = logger ?? throw new System.ArgumentNullException(nameof(logger));
            _accountProvider = accountProvider ?? throw new System.ArgumentNullException(nameof(accountProvider));
            _tradingMode = tradingMode ?? throw new System.ArgumentNullException(nameof(tradingMode));
        }

        public async Task<int> RunAllScenariosAsync()
        {
            if (!ValidateSimulationEnvironment())
            {
                _logger.Error("E2E TESTS ABORTED: Simulation environment validation failed.");
                return 0;
            }

            _logger.Info("=== E2E TESTS STARTING ===");
            int passed = 0;

            string[] basicScenarios = { "tp_hit", "sl_hit", "session_end" };
            string[] featureScenarios = { "command_ack", "duplicate_detection", "position_sync", "order_modify", "multi_account" };

            foreach (var scenario in basicScenarios)
            {
                _logger.Info($"--- Testing scenario: {scenario} ---");
                try
                {
                    _network.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
                    await Task.Delay(1500);
                    _logger.Info($"[TEST] Scenario {scenario}: Completed");
                    passed++;
                }
                catch (System.Exception ex)
                {
                    _logger.Error($"[TEST] Scenario {scenario}: FAILED", ex);
                }
                await Task.Delay(500);
            }

            foreach (var scenario in featureScenarios)
            {
                _logger.Info($"--- Testing feature: {scenario} ---");
                try
                {
                    if (scenario == "multi_account")
                    {
                        var accountNames = _accountProvider.GetAccounts().Select(a => a.Name).ToList();
                        if (accountNames.Count == 0)
                        {
                            _logger.Warning("[TEST] multi_account: No accounts connected — skipping");
                            continue;
                        }
                        var testAccounts = accountNames.Count >= 2
                            ? accountNames.Take(2).ToList()
                            : new List<string> { accountNames[0], accountNames[0] };

                        var accounts = new JArray(testAccounts);
                        _logger.Info($"[TEST] multi_account: Using accounts {string.Join(", ", testAccounts)}");
                        _network.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0, accounts: accounts);
                        await Task.Delay(3000);
                    }
                    else
                    {
                        _network.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
                        await Task.Delay(1500);
                    }
                    _logger.Info($"[TEST] Feature {scenario}: Completed");
                    passed++;
                }
                catch (System.Exception ex)
                {
                    _logger.Error($"[TEST] Feature {scenario}: FAILED", ex);
                }
                await Task.Delay(500);
            }

            int total = basicScenarios.Length + featureScenarios.Length;
            _logger.Info($"=== E2E TESTS COMPLETE: {passed}/{total} scenarios completed ===");
            return passed;
        }

        private bool ValidateSimulationEnvironment()
        {
            var accounts = _accountProvider.GetAccounts();
            if (accounts.Count == 0)
            {
                _logger.Error("No accounts available for E2E testing");
                return false;
            }

            foreach (var acct in accounts)
            {
                string name = acct.Name;
                bool isSim = name.StartsWith("Sim", System.StringComparison.OrdinalIgnoreCase);
                bool isDemo = name.StartsWith("DEMO", System.StringComparison.OrdinalIgnoreCase);
                if (!isSim && !isDemo)
                {
                    _logger.Error($"E2E SAFETY BLOCK: Account '{name}' is not a simulation/demo account.");
                    return false;
                }
            }
            return true;
        }
    }
}
