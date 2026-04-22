//+------------------------------------------------------------------+
//|                                 Commands/TestStartHandler.mqh    |
//|  Handles test_start command: runs E2E test scenarios.            |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Application/E2ETestRunner.mqh"

//+------------------------------------------------------------------+
//| TestStartHandler — triggers E2E tests                            |
//+------------------------------------------------------------------+
class TestStartHandler : public ICommandHandler
{
private:
   IZmqNetwork     *m_network;
   ILogger         *m_logger;
   E2ETestRunner   *m_testRunner;

public:
   TestStartHandler(IZmqNetwork *network, ILogger *logger)
   {
      m_network = network;
      m_logger = logger;
      m_testRunner = new E2ETestRunner(network, logger);
   }

   ~TestStartHandler()
   {
      if(m_testRunner != NULL)
      {
         delete m_testRunner;
         m_testRunner = NULL;
      }
   }

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_TEST_START);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(m_logger != NULL)
         m_logger->Info("Test start received — running E2E scenarios");

      if(m_testRunner != NULL)
         m_testRunner.RunAllScenarios();

      return true;
   }
};
