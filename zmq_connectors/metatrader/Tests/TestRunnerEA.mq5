//+------------------------------------------------------------------+
//|                                          TestRunnerEA.mq5        |
//|  Runs the MQL5 unit-test suites inside the terminal / tester.    |
//|                                                                  |
//|  OnInit executes every registered suite, prints the summary,     |
//|  and writes MQL_TESTS_RESULT.txt to Terminal\Common\Files so     |
//|  bin/run_mql_tests.sh can pick it up from WSL. In tester mode    |
//|  it stops the run immediately after the suites finish.           |
//|                                                                  |
//|  The include graph deliberately avoids ZmqNetwork.mqh and the    |
//|  vendor Zmq library so no DLL imports are needed in the tester.  |
//+------------------------------------------------------------------+
#property copyright "TradingBot"
#property version   "1.00"
#property strict

#include "SmokeTests.mqh"
#include "Suites/TestTickRateLimiter.mqh"
#include "Suites/TestAccountValidator.mqh"
#include "Suites/TestMessageEnvelope.mqh"
#include "Suites/TestConfigLoader.mqh"
#include "Suites/TestCommandDispatcher.mqh"
#include "Suites/TestSubscriptionManager.mqh"
#include "Suites/TestOrderTracking.mqh"
#include "Suites/TestSubscribeUnsubscribeDisconnect.mqh"
#include "Suites/TestOrderOpenHandler.mqh"
#include "Suites/TestOrderCloseHandler.mqh"
#include "Suites/TestOrderModifyHandler.mqh"
#include "Suites/TestRefreshRequestHandler.mqh"
#include "Suites/TestHistoryProvider.mqh"
#include "Suites/TestBrokerSync.mqh"
#include "Suites/TestConnectionWatchdog.mqh"
#include "Suites/TestMarketStreamer.mqh"
#include "Suites/TestE2ETestRunner.mqh"

//+------------------------------------------------------------------+
//| Expert initialization — run all suites, then report              |
//+------------------------------------------------------------------+
int OnInit()
{
   TestResultsReset();

   RunSmokeTests();
   RunTickRateLimiterTests();
   RunAccountValidatorTests();
   RunMessageEnvelopeTests();
   RunConfigLoaderTests();
   RunCommandDispatcherTests();
   RunSubscriptionManagerTests();
   RunOrderTrackingTests();
   RunSubscribeUnsubscribeDisconnectTests();
   RunOrderOpenHandlerTests();
   RunOrderCloseHandlerTests();
   RunOrderModifyHandlerTests();
   RunRefreshRequestHandlerTests();
   RunHistoryProviderTests();
   RunBrokerSyncTests();
   RunConnectionWatchdogTests();
   RunMarketStreamerTests();
   RunE2ETestRunnerTests();

   TestResultsSummary();

   if(!WriteResultsFile("MQL_TESTS_RESULT.txt"))
      Print("WARNING: could not write MQL_TESTS_RESULT.txt to Common\\Files");

   if((bool)MQLInfoInteger(MQL_TESTER))
      TesterStop();   //--- no need to replay ticks; results are in

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnTick — unused; suites run entirely in OnInit                   |
//+------------------------------------------------------------------+
void OnTick()
{
}
//+------------------------------------------------------------------+
