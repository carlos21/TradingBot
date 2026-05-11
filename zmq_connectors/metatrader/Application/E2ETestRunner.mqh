//+------------------------------------------------------------------+
//|                                Application/E2ETestRunner.mqh     |
//|  End-to-end test runner — orchestrates test scenarios.           |
//|  Mirrors NinjaTrader ZmqE2ETestRunner behavior.                  |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"

//+------------------------------------------------------------------+
//| E2ETestRunner — runs full E2E test suite                         |
//+------------------------------------------------------------------+
class E2ETestRunner
{
private:
   IZmqNetwork *m_network;
   ILogger     *m_logger;

public:
   E2ETestRunner(IZmqNetwork *network, ILogger *logger)
   {
      m_network = network;
      m_logger = logger;
   }

   ~E2ETestRunner() {}

   //--- Run all test scenarios
   void RunAllScenarios()
   {
      // Final safety validation
      if(!ValidateSimulationEnvironment())
      {
         if(m_logger != NULL)
            m_logger.Error("E2E TESTS ABORTED: Simulation environment validation failed.");
         return;
      }

      if(m_logger != NULL)
         m_logger.Info("=== E2E TESTS STARTING ===");

      int passed = 0;

      // Basic scenarios
      string basicScenarios[3];
      basicScenarios[0] = "tp_hit";
      basicScenarios[1] = "sl_hit";
      basicScenarios[2] = "session_end";

      // Feature scenarios (multi_account skipped — single-account platform)
      string featureScenarios[5];
      featureScenarios[0] = "command_ack";
      featureScenarios[1] = "duplicate_detection";
      featureScenarios[2] = "position_sync";
      featureScenarios[3] = "order_modify";
      featureScenarios[4] = "multi_account";

      // Run basic scenarios
      for(int i = 0; i < ArraySize(basicScenarios); i++)
      {
         string scenario = basicScenarios[i];
         if(m_logger != NULL)
            m_logger.Info("--- Testing scenario: " + scenario + " ---");

         m_network.SendTestStart(scenario, 21000.0, 80.0, 1.0);
         Sleep(1500);

         if(m_logger != NULL)
            m_logger.Info("[TEST] Scenario " + scenario + ": Completed");
         passed++;

         Sleep(500);
      }

      // Run feature scenarios
      for(int i = 0; i < ArraySize(featureScenarios); i++)
      {
         string scenario = featureScenarios[i];
         if(m_logger != NULL)
            m_logger.Info("--- Testing feature: " + scenario + " ---");

         if(scenario == "multi_account")
         {
            if(m_logger != NULL)
               m_logger.Warning("[TEST] multi_account: Skipped on MetaTrader (single-account platform)");
            continue;
         }

         m_network.SendTestStart(scenario, 21000.0, 80.0, 1.0);
         Sleep(1500);

         if(m_logger != NULL)
            m_logger.Info("[TEST] Feature " + scenario + ": Completed");
         passed++;

         Sleep(500);
      }

      int total = ArraySize(basicScenarios) + ArraySize(featureScenarios) - 1; // minus skipped multi_account
      if(m_logger != NULL)
      {
         m_logger.Info("=== E2E TESTS COMPLETE: " + IntegerToString(passed) + "/" + IntegerToString(total) + " scenarios completed ===");
         m_logger.Info("Note: Check logs above for any errors. 'Completed' means the scenario was orchestrated, not that all commands succeeded.");
      }
   }

private:
   //--- Validates that the account is a simulation/demo account
   bool ValidateSimulationEnvironment()
   {
      ENUM_ACCOUNT_TRADE_MODE tradeMode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
      bool isDemo = (tradeMode == ACCOUNT_TRADE_MODE_DEMO);
      bool isContest = (tradeMode == ACCOUNT_TRADE_MODE_CONTEST);

      if(!isDemo && !isContest)
      {
         string login = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
         if(m_logger != NULL)
            m_logger.Error("E2E SAFETY BLOCK: Account " + login + " is not a demo/contest account.");
         return false;
      }
      return true;
   }
};
