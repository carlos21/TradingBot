//+------------------------------------------------------------------+
//|                        Tests/Suites/TestBrokerSync.mqh           |
//|  Suite: BrokerSync — position restore into the tracker,          |
//|  position_sync reporting (tracked/untracked split), and          |
//|  deal-history fill detection with dedupe + FIFO bound.           |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Infrastructure/OrderTracking.mqh"
#include "../../Application/BrokerSync.mqh"

//+------------------------------------------------------------------+
//| RunBrokerSyncTests                                               |
//+------------------------------------------------------------------+
void RunBrokerSyncTests()
{
   //--- RestoreFromBroker: own-magic positions tracked, foreign skipped,
   //--- empty comment falls back to "mt5_<ticket>"
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(5001, 424242, "T-r1", (long)POSITION_TYPE_BUY, 1.10000, 1.09500, 1.11000, 1.0);
      position.AddPosition(5002, 999999, "T-foreign", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      position.AddPosition(5003, 424242, "", (long)POSITION_TYPE_SELL, 1.10100, 1.10600, 1.09100, 0.5);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.RestoreFromBroker();

      AssertEqualLong(2, tracker.GetActiveCount(), "BrokerSync: restore tracks own-magic positions only");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertTrue(tracker.TryGetEntry("T-r1", ticket, slPoints, rrRatio), "BrokerSync: restore keeps comment as trade_id");
      AssertEqualLong(5001, (long)ticket, "BrokerSync: restored trade carries position ticket");
      AssertTrue(tracker.TryGetEntry("mt5_5003", ticket, slPoints, rrRatio), "BrokerSync: empty comment gets mt5_ fallback id");
      AssertFalse(tracker.TryGetEntry("T-foreign", ticket, slPoints, rrRatio), "BrokerSync: foreign magic not restored");
      AssertTrue(logger.Contains("[Sync] Restored 2 position(s) from broker"), "BrokerSync: restore count logged");
      AssertEqualLong(0, network.SentCount(), "BrokerSync: restore sends nothing");
   }

   //--- RestoreFromBroker: nothing open → no log, no tracking
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.RestoreFromBroker();

      AssertEqualLong(0, tracker.GetActiveCount(), "BrokerSync: empty broker restores nothing");
      AssertEqualLong(0, logger.Count(), "BrokerSync: empty restore stays silent");
   }

   //--- ReportPositionsToPython: tracked/untracked split, full entry fields
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(6001, 424242, "T-p1", (long)POSITION_TYPE_BUY, 1.10000, 1.09500, 1.11000, 1.5);
      position.AddPosition(6002, 424242, "T-orphan", (long)POSITION_TYPE_SELL, 1.10100, 1.10600, 1.09100, 0.5);
      position.AddPosition(6003, 999999, "T-x", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      OrderStateManager tracker;
      tracker.TrackEntry("T-p1", 6001, 0, 1.0);

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ReportPositionsToPython();

      AssertEqualLong(1, network.SentCount(), "BrokerSync: report sends one message");
      AssertEqualString(MT_POSITION_SYNC, network.MsgTypeAt(0), "BrokerSync: report is position_sync");
      string payload = network.PayloadAt(0);
      AssertStringContains(payload, "\"count\":1", "BrokerSync: count present");
      AssertStringContains(payload, "\"trade_id\":\"T-p1\"", "BrokerSync: tracked entry carries trade_id");
      AssertStringContains(payload, "\"direction\":\"long\"", "BrokerSync: buy position reported long");
      AssertStringContains(payload, "\"entry_price\":1.10000000", "BrokerSync: entry price reported");
      AssertStringContains(payload, "\"stop_loss\":1.09500000", "BrokerSync: SL reported");
      AssertStringContains(payload, "\"take_profit\":1.11000000", "BrokerSync: TP reported");
      AssertStringContains(payload, "\"quantity\":1.50000000", "BrokerSync: quantity reported");
      AssertStringContains(payload, "\"account\":\"12345678\"", "BrokerSync: account is the fake login");
      AssertStringContains(payload, "\"trade_id\":\"T-orphan\"", "BrokerSync: untracked position listed");
      AssertStringContains(payload, "\"direction\":\"short\"", "BrokerSync: sell position reported short");
      AssertStringContains(payload, "\"source\":\"metatrader5\"", "BrokerSync: source field present");
      AssertStringContains(payload, "\"is_source_of_truth\":true", "BrokerSync: source-of-truth flag set");
      AssertTrue(StringFind(payload, "T-x") < 0, "BrokerSync: foreign magic excluded from report");
      AssertTrue(logger.Contains("[Sync] Reported 1 position(s) to Python"), "BrokerSync: report count logged");
      AssertTrue(logger.Contains("[Sync] Found 1 untracked position(s) on broker"), "BrokerSync: orphan warning logged");
   }

   //--- ReportPositionsToPython: no open positions still sends (count=0)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ReportPositionsToPython();

      AssertEqualLong(1, network.SentCount(MT_POSITION_SYNC), "BrokerSync: empty report still sends position_sync");
      AssertStringContains(network.PayloadAt(0), "\"count\":0", "BrokerSync: empty report count zero");
      AssertStringContains(network.PayloadAt(0), "\"positions\":[]", "BrokerSync: empty positions array");
      AssertEqualLong(0, logger.Count(), "BrokerSync: empty report stays silent");
   }

   //--- ProcessNewDeals: entry deal → entry_fill with SL/TP from the order
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      dealHistory.AddDeal(9001, 424242, (long)DEAL_ENTRY_IN, (long)DEAL_REASON_EXPERT,
                          1.10050, "T-e1", 8001, 1699999000, 1.5, 0.0, 0.0);
      dealHistory.SetOrderSlTp(8001, 1.09550, 1.11050);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();

      AssertEqualLong(1, network.SentCount(MT_ENTRY_FILL), "BrokerSync: entry deal sends one entry_fill");
      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(network.PayloadAtContains(fillIdx, "trade_id=T-e1"), "BrokerSync: entry fill uses comment as trade_id");
      AssertTrue(network.PayloadAtContains(fillIdx, "entry_price=1.100500"), "BrokerSync: entry fill carries deal price");
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=1.095500"), "BrokerSync: entry fill SL from linked order");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=1.110500"), "BrokerSync: entry fill TP from linked order");
      AssertTrue(network.PayloadAtContains(fillIdx, "quantity=1.500000"), "BrokerSync: entry fill carries volume");
      AssertTrue(network.PayloadAtContains(fillIdx, "account=12345678"), "BrokerSync: entry fill carries account");
      AssertTrue(network.PayloadAtContains(fillIdx, "balance=100000.000000"), "BrokerSync: entry fill carries balance");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(logIdx >= 0, "BrokerSync: entry deal logs trade_log");
      AssertTrue(network.PayloadAtContains(logIdx, "MT5:FILL"), "BrokerSync: trade_log event type recorded");
      AssertTrue(logger.Contains("ENTRY FILL: T-e1"), "BrokerSync: entry fill logged");
   }

   //--- ProcessNewDeals: entry deal without order SL/TP → zeros
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      dealHistory.AddDeal(9002, 424242, (long)DEAL_ENTRY_IN, (long)DEAL_REASON_EXPERT,
                          1.10050, "T-e2", 8002, 1699999000, 1.0, 0.0, 0.0);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();

      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=0.000000"), "BrokerSync: missing order SL reported zero");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=0.000000"), "BrokerSync: missing order TP reported zero");
   }

   //--- ProcessNewDeals: exit deals → exit_fill with reason mapping +
   //--- tracker cleanup (SL / TP / CLOSE)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      dealHistory.AddDeal(9010, 424242, (long)DEAL_ENTRY_OUT, (long)DEAL_REASON_SL,
                          1.09550, "T-x1", 8010, 1699999500, 1.0, -50.0, -2.0);
      dealHistory.AddDeal(9011, 424242, (long)DEAL_ENTRY_OUT, (long)DEAL_REASON_TP,
                          1.11050, "T-x2", 8011, 1699999600, 1.0, 105.0, -2.0);
      dealHistory.AddDeal(9012, 424242, (long)DEAL_ENTRY_OUT_BY, (long)DEAL_REASON_EXPERT,
                          1.10200, "T-x3", 8012, 1699999700, 1.0, 20.0, 0.0);
      OrderStateManager tracker;
      tracker.TrackEntry("T-x1", 6001, 0, 1.0);
      tracker.TrackEntry("T-x2", 6002, 0, 1.0);
      tracker.TrackEntry("T-x3", 6003, 0, 1.0);

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();

      AssertEqualLong(3, network.SentCount(MT_EXIT_FILL), "BrokerSync: three exit deals send three exit_fills");
      //--- Deals iterate newest-first (T-x3, T-x2, T-x1); each exit fill
      //--- is followed by its trade_log, so fills sit at indices 0/2/4.
      AssertEqualString(MT_EXIT_FILL, network.MsgTypeAt(0), "BrokerSync: newest exit fill first");
      AssertStringContains(network.PayloadAt(0), "trade_id=T-x3", "BrokerSync: newest exit processed first");
      AssertStringContains(network.PayloadAt(0), "result=CLOSE", "BrokerSync: OUT_BY + non-SL/TP reason maps to CLOSE");
      AssertEqualString(MT_EXIT_FILL, network.MsgTypeAt(2), "BrokerSync: second exit fill after first trade_log");
      AssertStringContains(network.PayloadAt(2), "trade_id=T-x2", "BrokerSync: second exit fill is T-x2");
      AssertStringContains(network.PayloadAt(2), "result=TP", "BrokerSync: DEAL_REASON_TP maps to TP");
      AssertEqualString(MT_EXIT_FILL, network.MsgTypeAt(4), "BrokerSync: oldest exit fill last");
      AssertStringContains(network.PayloadAt(4), "trade_id=T-x1", "BrokerSync: oldest exit fill is T-x1");
      AssertStringContains(network.PayloadAt(4), "result=SL", "BrokerSync: DEAL_REASON_SL maps to SL");
      AssertStringContains(network.PayloadAt(4), "pnl=-50.000000", "BrokerSync: exit fill carries realized pnl");
      AssertStringContains(network.PayloadAt(4), "commission=-2.000000", "BrokerSync: exit fill carries commission");
      AssertStringContains(network.PayloadAt(4), "exit_time=1699999500", "BrokerSync: exit fill carries deal time");
      AssertStringContains(network.PayloadAt(4), "balance=100000.000000", "BrokerSync: exit fill carries balance");

      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertFalse(tracker.TryGetEntry("T-x1", ticket, slPoints, rrRatio), "BrokerSync: SL exit removes tracker entry");
      AssertFalse(tracker.TryGetEntry("T-x2", ticket, slPoints, rrRatio), "BrokerSync: TP exit removes tracker entry");
      AssertFalse(tracker.TryGetEntry("T-x3", ticket, slPoints, rrRatio), "BrokerSync: CLOSE exit removes tracker entry");
      AssertTrue(logger.Contains("EXIT FILL (SL): T-x1"), "BrokerSync: SL exit logged");
      AssertTrue(logger.Contains("EXIT FILL (TP): T-x2"), "BrokerSync: TP exit logged");
   }

   //--- ProcessNewDeals: foreign-magic deals skipped entirely
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      dealHistory.AddDeal(9020, 999999, (long)DEAL_ENTRY_IN, (long)DEAL_REASON_EXPERT,
                          1.10050, "T-foreign", 8020, 1699999000, 1.0, 0.0, 0.0);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();

      AssertEqualLong(0, network.SentCount(), "BrokerSync: foreign-magic deal sends nothing");
      AssertEqualLong(0, logger.Count(), "BrokerSync: foreign-magic deal stays silent");
   }

   //--- ProcessNewDeals: already-processed tickets are not re-reported
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      dealHistory.AddDeal(9030, 424242, (long)DEAL_ENTRY_IN, (long)DEAL_REASON_EXPERT,
                          1.10050, "T-dup", 8030, 1699999000, 1.0, 0.0, 0.0);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();
      sync.ProcessNewDeals();

      AssertEqualLong(1, network.SentCount(MT_ENTRY_FILL), "BrokerSync: second pass skips processed deal");
   }

   //--- ProcessNewDeals: FIFO bound — beyond 1000 tracked deals the oldest
   //--- ticket is evicted, but iteration stops at the first processed deal
   //--- (newest→oldest, chronological), so no re-report cascade occurs
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeTimeApi time;
      time.SetNow((datetime)1700000000);
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      for(int i = 0; i < 1001; i++)
         dealHistory.AddDeal(100000 + i, 424242, (long)DEAL_ENTRY_IN, (long)DEAL_REASON_EXPERT,
                             1.10050, "T-f" + IntegerToString(i), 200000 + i,
                             1699999000 + i, 1.0, 0.0, 0.0);
      OrderStateManager tracker;

      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), 424242);
      sync.ProcessNewDeals();
      AssertEqualLong(1001, network.SentCount(MT_ENTRY_FILL), "BrokerSync: first pass fills all 1001 deals");

      sync.ProcessNewDeals();
      //--- Second pass hits the newest (already processed) deal and stops —
      //--- no re-report even though the FIFO overflowed on the first pass.
      AssertEqualLong(1001, network.SentCount(MT_ENTRY_FILL), "BrokerSync: overflow does not re-report");
   }
}
//+------------------------------------------------------------------+
