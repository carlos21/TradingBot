//+------------------------------------------------------------------+
//|                         Tests/Suites/TestOrderTracking.mqh       |
//|  Suite: OrderStateManager (IOrderTracker) — entry/SL/TP/close    |
//|  tracking, ticket reverse lookup, pending modifies, removal,     |
//|  and active-count/trade-id enumeration.                          |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../../Infrastructure/OrderTracking.mqh"

//+------------------------------------------------------------------+
//| RunOrderTrackingTests                                            |
//+------------------------------------------------------------------+
void RunOrderTrackingTests()
{
   //--- Track and retrieve an entry
   {
      OrderStateManager tracker;
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;

      AssertEqualLong(0, tracker.GetActiveCount(), "Tracker: starts empty");
      AssertFalse(tracker.TryGetEntry("T1", ticket, slPoints, rrRatio),
                  "Tracker: missing entry returns false");

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      AssertEqualLong(1, tracker.GetActiveCount(), "Tracker: count after track");
      AssertTrue(tracker.TryGetEntry("T1", ticket, slPoints, rrRatio),
                 "Tracker: tracked entry found");
      AssertEqualLong(1001, (long)ticket, "Tracker: entry ticket out");
      AssertEqualDouble(50.0, slPoints, 0.0001, "Tracker: entry slPoints out");
      AssertEqualDouble(2.0, rrRatio, 0.0001, "Tracker: entry rrRatio out");
   }

   //--- Re-tracking the same trade_id updates in place
   {
      OrderStateManager tracker;
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      tracker.TrackEntry("T1", 1002, 60.0, 3.0);

      AssertEqualLong(1, tracker.GetActiveCount(), "Tracker: duplicate trade_id not duplicated");
      AssertTrue(tracker.TryGetEntry("T1", ticket, slPoints, rrRatio),
                 "Tracker: updated entry found");
      AssertEqualLong(1002, (long)ticket, "Tracker: ticket updated");
      AssertEqualDouble(60.0, slPoints, 0.0001, "Tracker: slPoints updated");
      AssertEqualDouble(3.0, rrRatio, 0.0001, "Tracker: rrRatio updated");
   }

   //--- SL / TP / close-order tracking incl. duplicate update
   {
      OrderStateManager tracker;
      ulong ticket = 0;

      AssertFalse(tracker.TryGetStopLoss("T1", ticket), "Tracker: missing SL returns false");
      AssertFalse(tracker.TryGetTakeProfit("T1", ticket), "Tracker: missing TP returns false");
      AssertFalse(tracker.TryGetCloseOrder("T1", ticket), "Tracker: missing close returns false");

      tracker.TrackStopLoss("T1", 2001);
      tracker.TrackTakeProfit("T1", 3001);
      tracker.TrackCloseOrder("T1", 4001);

      AssertTrue(tracker.TryGetStopLoss("T1", ticket), "Tracker: SL found");
      AssertEqualLong(2001, (long)ticket, "Tracker: SL ticket out");
      AssertTrue(tracker.TryGetTakeProfit("T1", ticket), "Tracker: TP found");
      AssertEqualLong(3001, (long)ticket, "Tracker: TP ticket out");
      AssertTrue(tracker.TryGetCloseOrder("T1", ticket), "Tracker: close found");
      AssertEqualLong(4001, (long)ticket, "Tracker: close ticket out");

      tracker.TrackStopLoss("T1", 2002);
      AssertTrue(tracker.TryGetStopLoss("T1", ticket), "Tracker: SL still found after re-track");
      AssertEqualLong(2002, (long)ticket, "Tracker: SL ticket updated in place");
   }

   //--- Reverse lookup: ticket → trade_id across all record kinds
   {
      OrderStateManager tracker;
      string tradeId = "";

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      tracker.TrackStopLoss("T1", 2001);
      tracker.TrackTakeProfit("T2", 3001);
      tracker.TrackCloseOrder("T3", 4001);

      AssertTrue(tracker.TryGetTradeIdForTicket(1001, tradeId), "Tracker: entry ticket resolves");
      AssertEqualString("T1", tradeId, "Tracker: entry ticket maps to trade id");
      AssertTrue(tracker.TryGetTradeIdForTicket(2001, tradeId), "Tracker: SL ticket resolves");
      AssertEqualString("T1", tradeId, "Tracker: SL ticket maps to trade id");
      AssertTrue(tracker.TryGetTradeIdForTicket(3001, tradeId), "Tracker: TP ticket resolves");
      AssertEqualString("T2", tradeId, "Tracker: TP ticket maps to trade id");
      AssertTrue(tracker.TryGetTradeIdForTicket(4001, tradeId), "Tracker: close ticket resolves");
      AssertEqualString("T3", tradeId, "Tracker: close ticket maps to trade id");
      AssertFalse(tracker.TryGetTradeIdForTicket(9999, tradeId), "Tracker: unknown ticket not found");
   }

   //--- Pending modify set/get/update/remove
   {
      OrderStateManager tracker;
      double newSl = 0, newTp = 0;

      AssertFalse(tracker.TryGetPendingModify("T1", newSl, newTp),
                  "Tracker: missing pending modify returns false");

      tracker.SetPendingModify("T1", 1.11, 2.22);
      AssertTrue(tracker.TryGetPendingModify("T1", newSl, newTp), "Tracker: pending modify found");
      AssertEqualDouble(1.11, newSl, 0.0001, "Tracker: pending modify sl out");
      AssertEqualDouble(2.22, newTp, 0.0001, "Tracker: pending modify tp out");

      tracker.SetPendingModify("T1", 1.33, 4.44);
      AssertTrue(tracker.TryGetPendingModify("T1", newSl, newTp), "Tracker: updated modify found");
      AssertEqualDouble(1.33, newSl, 0.0001, "Tracker: pending modify sl updated");
      AssertEqualDouble(4.44, newTp, 0.0001, "Tracker: pending modify tp updated");

      tracker.RemovePendingModify("T1");
      AssertFalse(tracker.TryGetPendingModify("T1", newSl, newTp),
                  "Tracker: pending modify gone after remove");
      tracker.RemovePendingModify("UNKNOWN");  //--- must not crash
      AssertEqualLong(0, tracker.GetActiveCount(), "Tracker: pending modify never touches entries");
   }

   //--- RemoveTrade wipes every record kind for the trade
   {
      OrderStateManager tracker;
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0, newSl = 0, newTp = 0;

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      tracker.TrackEntry("T2", 5001, 25.0, 1.5);
      tracker.TrackStopLoss("T1", 2001);
      tracker.TrackTakeProfit("T1", 3001);
      tracker.TrackCloseOrder("T1", 4001);
      tracker.SetPendingModify("T1", 1.11, 2.22);

      tracker.RemoveTrade("T1");
      AssertEqualLong(1, tracker.GetActiveCount(), "Tracker: count drops after remove");
      AssertFalse(tracker.TryGetEntry("T1", ticket, slPoints, rrRatio), "Tracker: entry removed");
      AssertFalse(tracker.TryGetStopLoss("T1", ticket), "Tracker: SL removed");
      AssertFalse(tracker.TryGetTakeProfit("T1", ticket), "Tracker: TP removed");
      AssertFalse(tracker.TryGetCloseOrder("T1", ticket), "Tracker: close removed");
      AssertFalse(tracker.TryGetPendingModify("T1", newSl, newTp), "Tracker: pending modify removed");

      AssertTrue(tracker.TryGetEntry("T2", ticket, slPoints, rrRatio), "Tracker: other trade untouched");

      tracker.RemoveTrade("UNKNOWN");  //--- must not crash
      AssertEqualLong(1, tracker.GetActiveCount(), "Tracker: unknown remove keeps count");
   }

   //--- GetActiveTradeIds enumerates tracked entries
   {
      OrderStateManager tracker;
      string ids[];

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      tracker.TrackEntry("T2", 5001, 25.0, 1.5);
      tracker.TrackEntry("T3", 9001, 10.0, 1.0);

      tracker.GetActiveTradeIds(ids);
      AssertEqualLong(3, ArraySize(ids), "Tracker: three active ids");
      AssertEqualString("T1", ids[0], "Tracker: id order 0");
      AssertEqualString("T2", ids[1], "Tracker: id order 1");
      AssertEqualString("T3", ids[2], "Tracker: id order 2");

      tracker.RemoveTrade("T2");
      tracker.GetActiveTradeIds(ids);
      AssertEqualLong(2, ArraySize(ids), "Tracker: ids shrink after remove");
      AssertEqualString("T3", ids[1], "Tracker: remaining ids shifted");
   }

   //--- Clear wipes everything
   {
      OrderStateManager tracker;
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      string ids[];

      tracker.TrackEntry("T1", 1001, 50.0, 2.0);
      tracker.TrackStopLoss("T1", 2001);
      tracker.SetPendingModify("T1", 1.11, 2.22);

      tracker.Clear();
      AssertEqualLong(0, tracker.GetActiveCount(), "Tracker: count zero after clear");
      AssertFalse(tracker.TryGetEntry("T1", ticket, slPoints, rrRatio), "Tracker: entries cleared");
      AssertFalse(tracker.TryGetStopLoss("T1", ticket), "Tracker: SLs cleared");
      tracker.GetActiveTradeIds(ids);
      AssertEqualLong(0, ArraySize(ids), "Tracker: no ids after clear");
   }
}
//+------------------------------------------------------------------+
