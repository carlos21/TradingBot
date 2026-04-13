// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: TestStartHandler
// Handles TEST_START commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles TEST_START commands.
    /// </summary>
    internal sealed class TestStartHandler : ICommandHandler
    {
        public string CommandType => MessageType.TestStart;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;

        public TestStartHandler(ZmqNetwork network, ILogger logger)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var scenario = payload?["scenario"]?.ToString() ?? "tp_hit";
                var entryPrice = payload?["entry_price"]?.Value<double>() ?? 21000.0;
                var riskPoints = payload?["risk_points"]?.Value<double>() ?? 80.0;
                var rrRatio = payload?["rr_ratio"]?.Value<double>() ?? 1.0;

                _logger.Info($"TEST START: {scenario}");

                switch (scenario)
                {
                    case "command_ack":
                        RunCommandAckTest();
                        break;
                    case "duplicate_detection":
                        RunDuplicateDetectionTest();
                        break;
                    case "position_sync":
                        RunPositionSyncTest(entryPrice, riskPoints, rrRatio);
                        break;
                    case "order_modify":
                        RunOrderModifyTest(entryPrice, riskPoints, rrRatio);
                        break;
                    case "tp_hit":
                    case "sl_hit":
                    case "session_end":
                        RunBasicScenario(scenario, entryPrice, riskPoints, rrRatio);
                        break;
                    default:
                        _network?.SendTestResult(scenario, false, null, $"Unknown scenario: {scenario}");
                        break;
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Test start failed", ex);
                _network?.SendError("ninjatrader", "test_failed", $"Test start failed: {ex.Message}");
            }
        }

        private void RunCommandAckTest()
        {
            // Test that commands receive acknowledgments
            // Python will verify it receives command_ack messages
            _logger.Info("TEST: Command Acknowledgment - Python should verify acks are received");
            
            // Send a test result indicating this test requires Python-side verification
            _network?.SendTestResult("command_ack", true, null, 
                "Command ack flow active - verify Python receives acks for commands");
        }

        private void RunDuplicateDetectionTest()
        {
            // Simulate duplicate command detection
            // In real test, Python would send same command twice
            _logger.Info("TEST: Duplicate Detection - seq_num tracking active");
            
            _network?.SendTestResult("duplicate_detection", true, null,
                "Duplicate detection active - same seq_num will be rejected");
        }

        private void RunPositionSyncTest(double entryPrice, double riskPoints, double rrRatio)
        {
            // Simulate position sync after "crash"
            var tradeId = $"test_sync_{Guid.NewGuid().ToString("N").Substring(0, 8)}";
            var sl = entryPrice - riskPoints;
            var tp = entryPrice + (riskPoints * rrRatio);
            
            _logger.Info($"TEST: Position Sync - tradeId={tradeId}");
            
            // Simulate having an open position (as if we crashed with it open)
            _network?.SendEntryFill(tradeId, entryPrice, sl, tp);
            
            // Send position sync (as if we reconnected)
            Task.Run(async () =>
            {
                await Task.Delay(500);
                
                var positions = new Newtonsoft.Json.Linq.JArray();
                var position = new Newtonsoft.Json.Linq.JObject
                {
                    ["trade_id"] = tradeId,
                    ["direction"] = "long",
                    ["entry_price"] = entryPrice,
                    ["stop_loss"] = sl,
                    ["take_profit"] = tp,
                    ["quantity"] = 1
                };
                positions.Add(position);
                
                _network?.SendPositionSync(positions, null);
                _logger.Info($"TEST: Position sync sent for {tradeId}");
                
                await Task.Delay(500);
                _network?.SendTestResult("position_sync", true, tradeId, 
                    "Position sync sent - verify Python reconciles correctly");
            });
        }

        private void RunOrderModifyTest(double entryPrice, double riskPoints, double rrRatio)
        {
            // Simulate full order modify flow
            var tradeId = $"test_modify_{Guid.NewGuid().ToString("N").Substring(0, 8)}";
            var sl = entryPrice - riskPoints;
            var tp = entryPrice + (riskPoints * rrRatio);
            var newSl = entryPrice - (riskPoints / 2); // Move SL closer
            
            _logger.Info($"TEST: Order Modify - tradeId={tradeId}");
            
            // Send entry fill
            _network?.SendEntryFill(tradeId, entryPrice, sl, tp);
            
            Task.Run(async () =>
            {
                await Task.Delay(500);
                
                // Simulate modify command being processed
                _logger.Info($"TEST: Simulating SL modify from {sl} to {newSl}");
                _network?.SendTradeLog(tradeId, "NT:MODIFY", $"Test SL changed from {sl} to {newSl}");
                
                await Task.Delay(500);
                
                // Simulate TP hit after modify
                _network?.SendExitFill(tradeId, tp, "TP");
                _network?.SendTestResult("order_modify", true, tradeId, 
                    "SL modified and TP hit - verify Python tracked modify correctly");
            });
        }

        private void RunBasicScenario(string scenario, double entryPrice, double riskPoints, double rrRatio)
        {
            var tradeId = $"test_{scenario}_{Guid.NewGuid().ToString("N").Substring(0, 8)}";
            _logger.Info($"TEST START: {scenario} tradeId={tradeId} entry={entryPrice}");

            var sl = entryPrice - riskPoints;
            var tp = entryPrice + (riskPoints * rrRatio);

            _network?.SendEntryFill(tradeId, entryPrice, sl, tp);
            _network?.SendTradeLog(tradeId, "NT:TEST", $"Test entry filled @ {entryPrice}");

            // Simulate outcome
            Task.Run(async () =>
            {
                await Task.Delay(1000);
                SimulateTestOutcome(scenario, tradeId, sl, tp, entryPrice);
            });
        }

        private void SimulateTestOutcome(string scenario, string tradeId, double sl, double tp, double entryPrice)
        {
            switch (scenario)
            {
                case "tp_hit":
                    _logger.Info($"TEST: Simulating TP hit @ {tp}");
                    _network?.SendExitFill(tradeId, tp, "TP");
                    _network?.SendTestResult(scenario, true, tradeId, "TP hit as expected");
                    break;
                case "sl_hit":
                    _logger.Info($"TEST: Simulating SL hit @ {sl}");
                    _network?.SendExitFill(tradeId, sl, "SL");
                    _network?.SendTestResult(scenario, true, tradeId, "SL hit as expected");
                    break;
                case "session_end":
                    var closePrice = entryPrice + 5.0;
                    _logger.Info($"TEST: Simulating session end close @ {closePrice}");
                    _network?.SendExitFill(tradeId, closePrice, "CLOSE");
                    _network?.SendTestResult(scenario, true, tradeId, "Session end close as expected");
                    break;
                default:
                    _network?.SendTestResult(scenario, false, tradeId, $"Unknown scenario: {scenario}");
                    break;
            }
        }
    }
}
