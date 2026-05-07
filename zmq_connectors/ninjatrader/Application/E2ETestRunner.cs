// ═══════════════════════════════════════════════════════════════════════
// Application Layer: E2E Test Runner
// Runs end-to-end tests via ZMQ
// ═══════════════════════════════════════════════════════════════════════

using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// E2E Test Runner - orchestrates test scenarios
    /// </summary>
    internal sealed class ZmqE2ETestRunner
    {
        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;

        public ZmqE2ETestRunner(ZmqNetwork network, ILogger logger)
        {
            _network = network ?? throw new System.ArgumentNullException(nameof(network));
            _logger = logger ?? throw new System.ArgumentNullException(nameof(logger));
        }

        public async Task<int> RunAllScenariosAsync()
        {
            // Final safety validation inside the runner itself
            if (!ValidateSimulationEnvironment())
            {
                _logger.Error("E2E TESTS ABORTED: Simulation environment validation failed.");
                return 0;
            }

            _logger.Info("=== E2E TESTS STARTING ===");
            int passed = 0;
            
            // Basic scenarios
            string[] basicScenarios = { "tp_hit", "sl_hit", "session_end" };
            
            // New feature scenarios
            string[] featureScenarios = { "command_ack", "duplicate_detection", "position_sync", "order_modify", "multi_account" };
            
            // Run basic scenarios
            foreach (var scenario in basicScenarios)
            {
                _logger.Info($"--- Testing scenario: {scenario} ---");
                try
                {
                    _network?.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
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
            
            // Run feature scenarios
            foreach (var scenario in featureScenarios)
            {
                _logger.Info($"--- Testing feature: {scenario} ---");
                try
                {
                    if (scenario == "multi_account")
                    {
                        // Use actual connected accounts instead of hardcoded Sim101/Sim102
                        var accountNames = NinjaTrader.Cbi.Account.All.Select(a => a.Name).ToList();
                        if (accountNames.Count == 0)
                        {
                            _logger.Warning("[TEST] multi_account: No accounts connected — skipping");
                            continue;
                        }
                        // If only 1 account, test it twice to verify multi-account routing logic
                        var testAccounts = accountNames.Count >= 2
                            ? accountNames.Take(2).ToList()
                            : new System.Collections.Generic.List<string> { accountNames[0], accountNames[0] };

                        var accounts = new Newtonsoft.Json.Linq.JArray(testAccounts);
                        _logger.Info($"[TEST] multi_account: Using accounts {string.Join(", ", testAccounts)}");
                        _network?.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0, accounts: accounts);
                        await Task.Delay(3000); // Extra time for multi-order round-trip
                    }
                    else
                    {
                        _network?.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
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
            _logger.Info("Note: Check logs above for any errors. 'Completed' means the scenario was orchestrated, not that all commands succeeded.");
            return passed;
        }

        /// <summary>
        /// Validates that all connected accounts are simulation or demo accounts.
        /// This is the last line of defense before tests send orders.
        /// </summary>
        private bool ValidateSimulationEnvironment()
        {
            if (NinjaTrader.Cbi.Account.All.Count == 0)
            {
                _logger.Error("No accounts available for E2E testing");
                return false;
            }

            foreach (var acct in NinjaTrader.Cbi.Account.All)
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
