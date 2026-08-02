//+------------------------------------------------------------------+
//|                      Tests/Suites/TestE2ETestRunner.mqh          |
//|  Suite: TestStartHandler scenario dispatch (basic/feature/skip/  |
//|  unknown) and E2ETestRunner orchestration — scenario list,       |
//|  multi_account skip, completion summary.                         |
//|                                                                  |
//|  NOTE: E2ETestRunner's demo-account guard reads                  |
//|  AccountInfoInteger directly (no IAccountApi seam), so only the  |
//|  tester-emulated demo-account path is exercisable here — the     |
//|  non-demo refusal path is listed as excluded in COVERAGE.md.     |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"

//--- g_e2eTestRunning is re-declared in TestOrderOpenHandler.mqh,
//--- which TestRunnerEA includes before this suite.

#include "../../Commands/TestStartHandler.mqh"
#include "../../Application/E2ETestRunner.mqh"

//+------------------------------------------------------------------+
//| _MakeTestStartEnv — {"msg_type":"test_start","payload":<json>}   |
//+------------------------------------------------------------------+
MessageEnvelope *_MakeTestStartEnv(string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + MT_TEST_START + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunE2ETestRunnerTests                                            |
//+------------------------------------------------------------------+
void RunE2ETestRunnerTests()
{
   //--- CanHandle claims only test_start
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      AssertTrue(handler.CanHandle(MT_TEST_START), "E2E: CanHandle test_start");
      AssertFalse(handler.CanHandle(MT_ORDER_OPEN), "E2E: CanHandle rejects order_open");
      AssertFalse(handler.CanHandle(MT_SUBSCRIBE), "E2E: CanHandle rejects subscribe");
   }

   //--- NULL / rootless envelope → false + warning
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      AssertFalse(handler.Handle(NULL), "E2E: NULL envelope returns false");
      AssertTrue(logger.Contains("TestStartHandler: empty envelope"), "E2E: NULL envelope logged");
      MessageEnvelope broken;   //--- root == NULL
      AssertFalse(handler.Handle(GetPointer(broken)), "E2E: rootless envelope returns false");
      AssertEqualLong(0, network.SentCount(), "E2E: broken envelopes send nothing");
   }

   //--- multi_account: skipped with the single-account explanation
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env = _MakeTestStartEnv("{\"scenario\":\"multi_account\"}");
      AssertTrue(handler.Handle(env), "E2E: multi_account handled as skip");
      AssertEqualLong(1, network.SentCount(MT_TEST_RESULT), "E2E: multi_account sends one test_result");
      int idx = network.FindByMsgType(MT_TEST_RESULT);
      AssertTrue(network.PayloadAtContains(idx, "scenario=multi_account"), "E2E: result carries scenario");
      AssertTrue(network.PayloadAtContains(idx, "passed=true"), "E2E: skip reported as passed");
      AssertTrue(network.PayloadAtContains(idx, "single-account terminal"), "E2E: skip cites single-account terminal");
      AssertTrue(network.PayloadAtContains(idx, "account validated against login/accountName"),
                 "E2E: skip cites login validation");
      AssertTrue(logger.Contains("single-account terminal"), "E2E: skip warning logged");
      delete env;
   }

   //--- Unknown scenario → false + failure test_result
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env = _MakeTestStartEnv("{\"scenario\":\"bogus\"}");
      AssertFalse(handler.Handle(env), "E2E: unknown scenario returns false");
      int idx = network.FindByMsgType(MT_TEST_RESULT);
      AssertTrue(idx >= 0, "E2E: unknown scenario sends test_result");
      AssertTrue(network.PayloadAtContains(idx, "passed=false"), "E2E: unknown scenario fails");
      AssertTrue(network.PayloadAtContains(idx, "Unknown scenario: bogus"), "E2E: unknown scenario named");
      delete env;
   }

   //--- command_ack + duplicate_detection dispatch (registry entries)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env1 = _MakeTestStartEnv("{\"scenario\":\"command_ack\"}");
      AssertTrue(handler.Handle(env1), "E2E: command_ack returns true");
      AssertTrue(g_e2eTestRunning, "E2E: handle forces simulate mode flag");
      delete env1;

      MessageEnvelope *env2 = _MakeTestStartEnv("{\"scenario\":\"duplicate_detection\"}");
      AssertTrue(handler.Handle(env2), "E2E: duplicate_detection returns true");
      delete env2;

      AssertEqualLong(2, network.SentCount(MT_TEST_RESULT), "E2E: both ack scenarios report results");
      AssertStringContains(network.PayloadAt(0), "scenario=command_ack", "E2E: command_ack result first");
      AssertStringContains(network.PayloadAt(0), "passed=true", "E2E: command_ack passes");
      AssertStringContains(network.PayloadAt(1), "scenario=duplicate_detection", "E2E: duplicate_detection result");
      AssertStringContains(network.PayloadAt(1), "passed=true", "E2E: duplicate_detection passes");
   }

   //--- tp_hit: entry fill → TP exit fill → passing result
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env = _MakeTestStartEnv("{\"scenario\":\"tp_hit\"}");
      AssertTrue(handler.Handle(env), "E2E: tp_hit returns true");
      delete env;

      int entryIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(entryIdx >= 0, "E2E: tp_hit sends entry fill");
      AssertTrue(network.PayloadAtContains(entryIdx, "trade_id=test_tp_hit_"), "E2E: tp_hit trade id prefixed");
      AssertTrue(network.PayloadAtContains(entryIdx, "entry_price=21000.000000"), "E2E: tp_hit default entry");
      AssertTrue(network.PayloadAtContains(entryIdx, "sl=20920.000000"), "E2E: tp_hit SL below entry");
      AssertTrue(network.PayloadAtContains(entryIdx, "tp=21080.000000"), "E2E: tp_hit TP above entry");
      int exitIdx = network.FindByMsgType(MT_EXIT_FILL);
      AssertTrue(network.PayloadAtContains(exitIdx, "exit_price=21080.000000"), "E2E: tp_hit exits at TP");
      AssertTrue(network.PayloadAtContains(exitIdx, "result=TP"), "E2E: tp_hit result TP");
      int resIdx = network.FindByMsgType(MT_TEST_RESULT);
      AssertTrue(network.PayloadAtContains(resIdx, "scenario=tp_hit"), "E2E: tp_hit result scenario");
      AssertTrue(network.PayloadAtContains(resIdx, "passed=true"), "E2E: tp_hit passes");
      AssertTrue(network.PayloadAtContains(resIdx, "TP hit as expected"), "E2E: tp_hit result message");
   }

   //--- sl_hit + session_end outcomes
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env1 = _MakeTestStartEnv("{\"scenario\":\"sl_hit\"}");
      AssertTrue(handler.Handle(env1), "E2E: sl_hit returns true");
      delete env1;
      MessageEnvelope *env2 = _MakeTestStartEnv("{\"scenario\":\"session_end\"}");
      AssertTrue(handler.Handle(env2), "E2E: session_end returns true");
      delete env2;

      AssertEqualLong(2, network.SentCount(MT_EXIT_FILL), "E2E: both basic scenarios exit");
      //--- Each scenario sends entry_fill, trade_log, exit_fill, test_result
      AssertStringContains(network.PayloadAt(2), "exit_price=20920.000000", "E2E: sl_hit exits at SL");
      AssertStringContains(network.PayloadAt(2), "result=SL", "E2E: sl_hit result SL");
      AssertStringContains(network.PayloadAt(6), "exit_price=21005.000000", "E2E: session_end exits at entry+5");
      AssertStringContains(network.PayloadAt(6), "result=CLOSE", "E2E: session_end result CLOSE");
      AssertEqualLong(2, network.SentCount(MT_TEST_RESULT), "E2E: both basic scenarios report");
      AssertStringContains(network.PayloadAt(3), "SL hit as expected", "E2E: sl_hit result message");
      AssertStringContains(network.PayloadAt(7), "Session end close as expected", "E2E: session_end result message");
   }

   //--- order_modify: entry → modify trade_log → TP exit → result
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env = _MakeTestStartEnv("{\"scenario\":\"order_modify\"}");
      AssertTrue(handler.Handle(env), "E2E: order_modify returns true");
      delete env;

      AssertEqualLong(1, network.SentCount(MT_ENTRY_FILL), "E2E: order_modify sends entry fill");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(network.PayloadAtContains(logIdx, "MT5:MODIFY"), "E2E: order_modify logs modify event");
      AssertTrue(network.PayloadAtContains(logIdx, "Test SL changed from 20920.00000 to 20960.00000"),
                 "E2E: order_modify halves the SL distance");
      int exitIdx = network.FindByMsgType(MT_EXIT_FILL);
      AssertTrue(network.PayloadAtContains(exitIdx, "result=TP"), "E2E: order_modify exits at TP");
      int resIdx = network.FindByMsgType(MT_TEST_RESULT);
      AssertTrue(network.PayloadAtContains(resIdx, "scenario=order_modify"), "E2E: order_modify result scenario");
      AssertTrue(network.PayloadAtContains(resIdx, "passed=true"), "E2E: order_modify passes");
   }

   //--- position_sync: entry fill → position_sync JSON → result
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      TestStartHandler handler(GetPointer(network), GetPointer(logger));

      MessageEnvelope *env = _MakeTestStartEnv("{\"scenario\":\"position_sync\"}");
      AssertTrue(handler.Handle(env), "E2E: position_sync returns true");
      delete env;

      AssertEqualLong(1, network.SentCount(MT_ENTRY_FILL), "E2E: position_sync sends entry fill");
      int syncIdx = network.FindByMsgType(MT_POSITION_SYNC);
      AssertTrue(syncIdx >= 0, "E2E: position_sync sends sync message");
      string payload = network.PayloadAt(syncIdx);
      AssertTrue(StringFind(payload, "\"trade_id\":\"test_sync_") >= 0, "E2E: sync carries test trade id");
      AssertStringContains(payload, "\"direction\":\"long\"", "E2E: sync position is long");
      AssertStringContains(payload, "\"entry_price\":21000", "E2E: sync carries entry price");
      AssertStringContains(payload, "\"quantity\":0.10000000", "E2E: sync carries quantity");
      int resIdx = network.FindByMsgType(MT_TEST_RESULT);
      AssertTrue(network.PayloadAtContains(resIdx, "scenario=position_sync"), "E2E: position_sync result scenario");
      AssertTrue(network.PayloadAtContains(resIdx, "passed=true"), "E2E: position_sync passes");
   }

   //--- E2ETestRunner.RunAllScenarios: 3 basic + 4 feature scenarios
   //--- orchestrated, multi_account skipped (tester account is demo)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      account.SetIsDemo(true);
      E2ETestRunner runner(GetPointer(network), GetPointer(logger), GetPointer(account));
      runner.RunAllScenarios();

      AssertEqualLong(7, network.SentCount(MT_TEST_START), "E2E: runner starts 7 scenarios");
      AssertStringContains(network.PayloadAt(0), "scenario=tp_hit", "E2E: runner starts with tp_hit");
      AssertStringContains(network.PayloadAt(0), "entry_price=21000.000000", "E2E: runner test entry price");
      AssertStringContains(network.PayloadAt(0), "risk_points=80.000000", "E2E: runner test risk points");
      AssertStringContains(network.PayloadAt(1), "scenario=sl_hit", "E2E: runner second scenario sl_hit");
      AssertStringContains(network.PayloadAt(2), "scenario=session_end", "E2E: runner third scenario session_end");
      AssertStringContains(network.PayloadAt(3), "scenario=command_ack", "E2E: runner feature command_ack");
      AssertStringContains(network.PayloadAt(4), "scenario=duplicate_detection", "E2E: runner feature duplicate_detection");
      AssertStringContains(network.PayloadAt(5), "scenario=position_sync", "E2E: runner feature position_sync");
      AssertStringContains(network.PayloadAt(6), "scenario=order_modify", "E2E: runner feature order_modify");
      AssertTrue(logger.Contains("E2E TESTS STARTING"), "E2E: runner start logged");
      AssertTrue(logger.Contains("multi_account: Skipped on MetaTrader (single-account terminal; account validated against login/accountName)"),
                 "E2E: runner logs multi_account skip reason");
      AssertTrue(logger.Contains("E2E TESTS COMPLETE: 7/7 scenarios completed"), "E2E: runner completion summary");
   }

   //--- RunAllScenarios: non-demo account is refused (safety guard)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      account.SetIsDemo(false);
      account.SetLogin(999888);
      E2ETestRunner runner(GetPointer(network), GetPointer(logger), GetPointer(account));
      runner.RunAllScenarios();

      AssertEqualLong(0, network.SentCount(MT_TEST_START), "E2E: no scenarios start on non-demo account");
      AssertTrue(logger.Contains("E2E SAFETY BLOCK: Account 999888 is not a demo/contest account."),
                 "E2E: safety block logged for non-demo account");
      AssertTrue(logger.Contains("E2E TESTS ABORTED"), "E2E: abort logged for non-demo account");
   }
}
//+------------------------------------------------------------------+
