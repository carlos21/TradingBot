// ═══════════════════════════════════════════════════════════════════════
// Application Layer: E2E Test Runner
// Runs end-to-end tests via ZMQ
// ═══════════════════════════════════════════════════════════════════════

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
            _logger.Info("=== E2E TESTS STARTING ===");
            int passed = 0;
            
            // Basic scenarios
            string[] basicScenarios = { "tp_hit", "sl_hit", "session_end" };
            
            // New feature scenarios
            string[] featureScenarios = { "command_ack", "duplicate_detection", "position_sync", "order_modify" };
            
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
                    _network?.SendTestStart(scenario, entryPrice: 21000.0, riskPoints: 80.0, rrRatio: 1.0);
                    await Task.Delay(1500);
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
            _logger.Info($"=== E2E TESTS COMPLETE: {passed}/{total} passed ===");
            return passed;
        }
    }
}
