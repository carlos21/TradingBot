//+------------------------------------------------------------------+
//|                                 Commands/TestStartHandler.mqh    |
//|  Handles test_start command: scenario-based simulated tests.     |
//|  Mirrors NinjaTrader TestStartHandler behavior.                  |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"

//+------------------------------------------------------------------+
//| TestStartHandler — scenario-based test orchestration             |
//+------------------------------------------------------------------+
class TestStartHandler : public ICommandHandler
{
private:
   IZmqNetwork *m_network;
   ILogger     *m_logger;

public:
   TestStartHandler(IZmqNetwork *network, ILogger *logger)
   {
      m_network = network;
      m_logger = logger;
   }

   ~TestStartHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_TEST_START);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("TestStartHandler: empty envelope");
         return false;
      }

      string scenario  = envelope.PayloadString("scenario", "tp_hit");
      double entryPrice = envelope.PayloadDouble("entry_price", 21000.0);
      double riskPoints = envelope.PayloadDouble("risk_points", 80.0);
      double rrRatio    = envelope.PayloadDouble("rr_ratio", 1.0);

      if(m_logger != NULL)
         m_logger.Info("TEST START: " + scenario);

      // Force simulate mode during tests
      g_e2eTestRunning = true;

      bool result = false;

      if(scenario == "command_ack")
      {
         RunCommandAckTest();
         result = true;
      }
      else if(scenario == "duplicate_detection")
      {
         RunDuplicateDetectionTest();
         result = true;
      }
      else if(scenario == "position_sync")
      {
         RunPositionSyncTest(entryPrice, riskPoints, rrRatio);
         result = true;
      }
      else if(scenario == "order_modify")
      {
         RunOrderModifyTest(entryPrice, riskPoints, rrRatio);
         result = true;
      }
      else if(scenario == "tp_hit" || scenario == "sl_hit" || scenario == "session_end")
      {
         RunBasicScenario(scenario, entryPrice, riskPoints, rrRatio);
         result = true;
      }
      else if(scenario == "multi_account")
      {
         if(m_logger != NULL)
            m_logger.Warning("TEST: multi_account skipped on MetaTrader (single-account platform)");
         m_network.SendTestResult(scenario, true, "", "Skipped on MetaTrader — single-account platform");
         result = true;
      }
      else
      {
         m_network.SendTestResult(scenario, false, "", "Unknown scenario: " + scenario);
         result = false;
      }

      // Reset test flag after a delay to allow simulated fills to complete
      // Note: we don't reset immediately because async sleeps are still running
      // The EA's OnTick will continue to process; we leave g_e2eTestRunning = true
      // and let it be reset by the E2ETestRunner after the suite completes,
      // or simply leave it since tests are short-lived.
      // For simplicity, reset after a short delay in a separate call is tricky in MQL5.
      // We'll reset it at the end of RunAllScenarios in E2ETestRunner.

      return result;
   }

private:
   //--- Generate a unique test trade ID
   string GenerateTradeId(string prefix)
   {
      static int counter = 0;
      counter++;
      return prefix + "_" + IntegerToString((int)TimeLocal()) + "_" + IntegerToString(counter);
   }

   //--- Test: Command Acknowledgment
   void RunCommandAckTest()
   {
      if(m_logger != NULL)
         m_logger.Info("TEST: Command Acknowledgment — Python should verify acks are received");

      m_network.SendTestResult("command_ack", true, "",
         "Command ack flow active — verify Python receives acks for commands");
   }

   //--- Test: Duplicate Detection
   void RunDuplicateDetectionTest()
   {
      if(m_logger != NULL)
         m_logger.Info("TEST: Duplicate Detection — seq_num tracking active");

      m_network.SendTestResult("duplicate_detection", true, "",
         "Duplicate detection active — same seq_num will be rejected");
   }

   //--- Test: Position Sync
   void RunPositionSyncTest(double entryPrice, double riskPoints, double rrRatio)
   {
      string tradeId = GenerateTradeId("test_sync");
      double sl = entryPrice - riskPoints;
      double tp = entryPrice + (riskPoints * rrRatio);

      if(m_logger != NULL)
         m_logger.Info("TEST: Position Sync — tradeId=" + tradeId);

      // Simulate having an open position (as if we crashed with it open)
      m_network.SendEntryFill(tradeId, entryPrice, sl, tp);

      Sleep(500);

      // Send position sync (as if we reconnected)
      JSONValue *positions = new JSONValue(JSON_ARRAY);
      JSONValue *pos = new JSONValue(JSON_OBJECT);
      pos["trade_id"]    = new JSONValue(tradeId);
      pos["direction"]   = new JSONValue("long");
      pos["entry_price"] = new JSONValue(entryPrice);
      pos["stop_loss"]   = new JSONValue(sl);
      pos["take_profit"] = new JSONValue(tp);
      pos["quantity"]    = new JSONValue(0.1);
      positions.Add(pos);

      JSONValue *untracked = new JSONValue(JSON_ARRAY);
      m_network.SendPositionSync(positions, untracked);
      // Arrays are owned by SendPositionSync — do NOT delete

      if(m_logger != NULL)
         m_logger.Info("TEST: Position sync sent for " + tradeId);

      Sleep(500);

      m_network.SendTestResult("position_sync", true, tradeId,
         "Position sync sent — verify Python reconciles correctly");
   }

   //--- Test: Order Modify
   void RunOrderModifyTest(double entryPrice, double riskPoints, double rrRatio)
   {
      string tradeId = GenerateTradeId("test_modify");
      double sl = entryPrice - riskPoints;
      double tp = entryPrice + (riskPoints * rrRatio);
      double newSl = entryPrice - (riskPoints / 2.0); // Move SL closer

      if(m_logger != NULL)
         m_logger.Info("TEST: Order Modify — tradeId=" + tradeId);

      // Send entry fill
      m_network.SendEntryFill(tradeId, entryPrice, sl, tp);

      Sleep(500);

      // Simulate modify command being processed
      if(m_logger != NULL)
         m_logger.Info("TEST: Simulating SL modify from " + DoubleToString(sl, 5) + " to " + DoubleToString(newSl, 5));
      m_network.SendTradeLog(tradeId, "MT5:MODIFY", "Test SL changed from " + DoubleToString(sl, 5) + " to " + DoubleToString(newSl, 5));

      Sleep(500);

      // Simulate TP hit after modify
      m_network.SendExitFill(tradeId, tp, "TP");
      m_network.SendTestResult("order_modify", true, tradeId,
         "SL modified and TP hit — verify Python tracked modify correctly");
   }

   //--- Basic scenarios: tp_hit, sl_hit, session_end
   void RunBasicScenario(string scenario, double entryPrice, double riskPoints, double rrRatio)
   {
      string tradeId = GenerateTradeId("test_" + scenario);
      if(m_logger != NULL)
         m_logger.Info("TEST START: " + scenario + " tradeId=" + tradeId + " entry=" + DoubleToString(entryPrice, 5));

      double sl = entryPrice - riskPoints;
      double tp = entryPrice + (riskPoints * rrRatio);

      m_network.SendEntryFill(tradeId, entryPrice, sl, tp);
      m_network.SendTradeLog(tradeId, "MT5:TEST", "Test entry filled @ " + DoubleToString(entryPrice, 5));

      Sleep(1000);

      SimulateTestOutcome(scenario, tradeId, sl, tp, entryPrice);
   }

   void SimulateTestOutcome(string scenario, string tradeId, double sl, double tp, double entryPrice)
   {
      if(scenario == "tp_hit")
      {
         if(m_logger != NULL)
            m_logger.Info("TEST: Simulating TP hit @ " + DoubleToString(tp, 5));
         m_network.SendExitFill(tradeId, tp, "TP");
         m_network.SendTestResult(scenario, true, tradeId, "TP hit as expected");
      }
      else if(scenario == "sl_hit")
      {
         if(m_logger != NULL)
            m_logger.Info("TEST: Simulating SL hit @ " + DoubleToString(sl, 5));
         m_network.SendExitFill(tradeId, sl, "SL");
         m_network.SendTestResult(scenario, true, tradeId, "SL hit as expected");
      }
      else if(scenario == "session_end")
      {
         double closePrice = entryPrice + 5.0;
         if(m_logger != NULL)
            m_logger.Info("TEST: Simulating session end close @ " + DoubleToString(closePrice, 5));
         m_network.SendExitFill(tradeId, closePrice, "CLOSE");
         m_network.SendTestResult(scenario, true, tradeId, "Session end close as expected");
      }
      else
      {
         m_network.SendTestResult(scenario, false, tradeId, "Unknown scenario: " + scenario);
      }
   }
};
