//+------------------------------------------------------------------+
//|                                Application/E2ETestRunner.mqh     |
//|  Basic end-to-end test scenarios.                                |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"

//+------------------------------------------------------------------+
//| E2ETestRunner — runs connectivity and basic trade tests          |
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
      if(m_logger != NULL)
         m_logger->Info("=== E2E TESTS START ===");

      TestPingPong();
      TestSendTick();
      TestSendBar();

      if(m_logger != NULL)
         m_logger->Info("=== E2E TESTS COMPLETE ===");
   }

   //--- Test 1: REQ/REP ping/pong
   void TestPingPong()
   {
      if(m_logger != NULL)
         m_logger->Info("[E2E] Testing ping/pong...");

      bool ok = m_network.SendTestPingWithResponse(2000);
      if(ok)
      {
         if(m_logger != NULL)
            m_logger->Success("[E2E] Ping/Pong: PASSED");
      }
      else
      {
         if(m_logger != NULL)
            m_logger->Warning("[E2E] Ping/Pong: FAILED");
      }
   }

   //--- Test 2: Send a dummy tick
   void TestSendTick()
   {
      if(m_logger != NULL)
         m_logger->Info("[E2E] Testing tick send...");

      m_network.SendTick("TEST", 1.12345, 100, TimeCurrent());

      if(m_logger != NULL)
         m_logger->Success("[E2E] Tick send: PASSED");
   }

   //--- Test 3: Send a dummy bar
   void TestSendBar()
   {
      if(m_logger != NULL)
         m_logger->Info("[E2E] Testing bar send...");

      datetime now = TimeCurrent();
      m_network.SendBar("TEST", now, 1.12000, 1.12500, 1.11900, 1.12300, 500, false);

      if(m_logger != NULL)
         m_logger->Success("[E2E] Bar send: PASSED");
   }
};
