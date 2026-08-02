//+------------------------------------------------------------------+
//|                     Tests/Suites/TestOrderCloseHandler.mqh       |
//|  Suite: OrderCloseHandler — payload validation, simulate exit    |
//|  fills, live close via tracker ticket / comment fallback,        |
//|  position-not-found cleanup, broker success/failure paths.       |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Infrastructure/OrderTracking.mqh"

//--- g_e2eTestRunning is re-declared in TestOrderOpenHandler.mqh,
//--- which TestRunnerEA includes before this suite.

#include "../../Commands/OrderCloseHandler.mqh"

//+------------------------------------------------------------------+
//| _MakeOrderCloseEnv — {"msg_type":"order_close","payload":<json>} |
//+------------------------------------------------------------------+
MessageEnvelope *_MakeOrderCloseEnv(string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + MT_ORDER_CLOSE + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunOrderCloseHandlerTests                                        |
//+------------------------------------------------------------------+
void RunOrderCloseHandlerTests()
{
   //--- CanHandle claims only "order_close"
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      AssertTrue(handler.CanHandle(MT_ORDER_CLOSE), "OrderClose: CanHandle order_close");
      AssertFalse(handler.CanHandle(MT_ORDER_OPEN), "OrderClose: CanHandle rejects order_open");
      AssertFalse(handler.CanHandle(MT_ORDER_MODIFY), "OrderClose: CanHandle rejects order_modify");
      delete apis;
   }

   //--- NULL / rootless envelope → false, nothing sent
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      AssertFalse(handler.Handle(NULL), "OrderClose: NULL envelope returns false");
      AssertTrue(logger.Contains("OrderCloseHandler: empty envelope"), "OrderClose: NULL envelope logged");

      MessageEnvelope broken;   //--- root == NULL
      AssertFalse(handler.Handle(GetPointer(broken)), "OrderClose: rootless envelope returns false");
      AssertEqualLong(0, network.SentCount(), "OrderClose: broken envelopes send nothing");
      delete apis;
   }

   //--- Missing trade_id → false, no network traffic
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderClose: missing trade_id returns false");
      AssertTrue(logger.Contains("OrderCloseHandler: missing trade_id"), "OrderClose: missing trade_id logged");
      AssertEqualLong(0, network.SentCount(), "OrderClose: missing trade_id sends nothing");
      delete env;
      delete apis;
   }

   //--- Missing instrument → false + order_close_failed error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-noinst\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderClose: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderClose: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "order_close_failed"), "OrderClose: error type recorded");
      delete env;
      delete apis;
   }

   //--- Account mismatch → false + account_mismatch error, no trade
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-badacct\",\"instrument\":\"EURUSD\",\"account\":\"99999999\"}");
      AssertFalse(handler.Handle(env), "OrderClose: account mismatch returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "account_mismatch"), "OrderClose: account_mismatch type recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: account mismatch sends no trade");
      delete env;
      delete apis;
   }

   //--- Unknown symbol → false + order_close_failed error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      symbols.SetSelectResult("NOPE", false);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-nope\",\"instrument\":\"NOPE\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderClose: unknown symbol returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "unknown symbol: NOPE"), "OrderClose: unknown-symbol message recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: unknown symbol sends no trade");
      delete env;
      delete apis;
   }

   //--- Simulate mode: exit fill at bid, no broker interaction
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis, true);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-sim\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: simulate close returns true");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: simulate sends no broker trade");
      int fillIdx = network.FindByMsgType(MT_EXIT_FILL);
      AssertTrue(fillIdx >= 0, "OrderClose: simulate records exit_fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "trade_id=T-sim"), "OrderClose: simulate fill carries trade_id");
      AssertTrue(network.PayloadAtContains(fillIdx, "exit_price=1.100000"), "OrderClose: simulate fill at bid");
      AssertTrue(network.PayloadAtContains(fillIdx, "result=CLOSE"), "OrderClose: simulate fill result CLOSE");
      AssertTrue(network.PayloadAtContains(fillIdx, "account=12345678"), "OrderClose: simulate fill echoes account");
      delete env;
      delete apis;
   }

   //--- Trade not tracked at all → ignored, ack-positive
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-unknown\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: untracked close returns true");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(logIdx >= 0, "OrderClose: untracked close logs trade_log");
      AssertTrue(network.PayloadAtContains(logIdx, "Close ignored: trade not tracked"), "OrderClose: untracked warning recorded");
      AssertEqualLong(0, network.SentCount(MT_EXIT_FILL), "OrderClose: untracked close sends no fill");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: untracked close sends no trade");
      delete env;
      delete apis;
   }

   //--- Live close of a long position via the tracked ticket
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(7001, 424242, "T-live1", (long)POSITION_TYPE_BUY, 1.10000, 1.09500, 1.11000, 1.5);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-live1", 7001, 50.0, 2.0);
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      trade.QueueResult(10009, 0, 1.09990, 0);
      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-live1\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: live close returns true");
      AssertEqualLong(1, trade.SendCount(), "OrderClose: live close sends one trade");
      AssertEqualLong((long)TRADE_ACTION_DEAL, trade.LastAction(), "OrderClose: request action DEAL");
      AssertEqualLong(7001, (long)trade.LastPosition(), "OrderClose: request targets position ticket");
      AssertEqualLong((long)ORDER_TYPE_SELL, trade.LastType(), "OrderClose: long position closed with SELL");
      AssertEqualDouble(1.10000, trade.LastPrice(), 0.000001, "OrderClose: close SELL priced at bid");
      AssertEqualDouble(1.5, trade.LastVolume(), 0.0001, "OrderClose: close volume from position");
      AssertEqualLong(424242, trade.LastMagic(), "OrderClose: magic from config");

      int fillIdx = network.FindByMsgType(MT_EXIT_FILL);
      AssertTrue(fillIdx >= 0, "OrderClose: live close sends exit_fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "exit_price=1.099900"), "OrderClose: fill at broker price");
      AssertTrue(network.PayloadAtContains(fillIdx, "result=CLOSE"), "OrderClose: fill result CLOSE");
      AssertTrue(network.PayloadAtContains(fillIdx, "balance=100000.000000"), "OrderClose: fill carries balance");

      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertFalse(tracker.TryGetEntry("T-live1", ticket, slPoints, rrRatio), "OrderClose: tracker entry removed");
      delete env;
      delete apis;
   }

   //--- Live close of a short position → BUY back at ask
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(7002, 424242, "T-live2", (long)POSITION_TYPE_SELL, 1.10000, 1.10500, 1.09000, 0.75);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-live2", 7002, 50.0, 2.0);
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      trade.QueueResult(10009, 0, 1.10010, 0);
      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-live2\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: short close returns true");
      AssertEqualLong((long)ORDER_TYPE_BUY, trade.LastType(), "OrderClose: short position closed with BUY");
      AssertEqualDouble(1.10010, trade.LastPrice(), 0.000001, "OrderClose: close BUY priced at ask");
      delete env;
      delete apis;
   }

   //--- Fallback: ticket found by scanning positions for the comment
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(8001, 424242, "T-fb", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      //--- tracker NULL → comment fallback drives the lookup
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), NULL,
                                GetPointer(config), apis);

      trade.QueueResult(10009, 0, 1.09990, 0);
      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-fb\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: comment fallback returns true");
      AssertEqualLong(8001, (long)trade.LastPosition(), "OrderClose: fallback found ticket by comment");
      AssertTrue(network.FindByMsgType(MT_EXIT_FILL) >= 0, "OrderClose: fallback close sends fill");
      delete env;
      delete apis;
   }

   //--- Fallback skips positions with a different magic number
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(8002, 999999, "T-othermagic", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), NULL,
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-othermagic\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: foreign-magic close returns true");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: foreign-magic position not touched");
      AssertTrue(logger.Contains("no open position"), "OrderClose: foreign-magic skip logged");
      AssertEqualLong(0, network.SentCount(MT_EXIT_FILL), "OrderClose: foreign-magic sends no fill");
      delete env;
      delete apis;
   }

   //--- Tracked but no position anywhere → tracker cleaned, true
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-gone", 7001, 50.0, 2.0);   //--- no scripted position
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-gone\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: vanished position returns true");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertFalse(tracker.TryGetEntry("T-gone", ticket, slPoints, rrRatio), "OrderClose: vanished trade untracked");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: vanished position sends no trade");
      AssertEqualLong(0, network.SentCount(MT_EXIT_FILL), "OrderClose: vanished position sends no fill");
      delete env;
      delete apis;
   }

   //--- SelectByTicket failure → tracker cleaned, true
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(7001, 424242, "T-other", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-stale", 9999, 50.0, 2.0);   //--- ticket 9999 unknown to position api
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-stale\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderClose: unselectable position returns true");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertFalse(tracker.TryGetEntry("T-stale", ticket, slPoints, rrRatio), "OrderClose: stale trade untracked");
      AssertEqualLong(0, trade.SendCount(), "OrderClose: unselectable position sends no trade");
      delete env;
      delete apis;
   }

   //--- Broker send failure → false + order_rejected + error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(7001, 424242, "T-fail", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-fail", 7001, 50.0, 2.0);
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      trade.QueueResult(10006, 0, 0, 0);
      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-fail\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderClose: broker failure returns false");
      int rejIdx = network.FindByMsgType(MT_ORDER_REJECTED);
      AssertTrue(rejIdx >= 0, "OrderClose: order_rejected recorded");
      AssertTrue(network.PayloadAtContains(rejIdx, "trade_id=T-fail"), "OrderClose: rejection carries trade_id");
      AssertTrue(network.PayloadAtContains(rejIdx, "Close OrderSend err="), "OrderClose: rejection carries reason");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "order_close_failed"), "OrderClose: failure error type recorded");
      delete env;
      delete apis;
   }

   //--- Send ok but non-DONE retcode → false + order_rejected
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTradeApi trade;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      position.AddPosition(7001, 424242, "T-rej", (long)POSITION_TYPE_BUY, 1.10000, 0, 0, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-rej", 7001, 50.0, 2.0);
      OrderCloseHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                GetPointer(config), apis);

      trade.QueueResult(10025, 0, 0, 0);
      trade.ForceSendReturn(true);
      MessageEnvelope *env = _MakeOrderCloseEnv(
         "{\"trade_id\":\"T-rej\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderClose: rejection retcode returns false");
      int rejIdx = network.FindByMsgType(MT_ORDER_REJECTED);
      AssertTrue(network.PayloadAtContains(rejIdx, "Close retcode=10025"), "OrderClose: retcode in rejection reason");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertTrue(tracker.TryGetEntry("T-rej", ticket, slPoints, rrRatio), "OrderClose: failed close keeps tracker entry");
      delete env;
      delete apis;
   }
}
//+------------------------------------------------------------------+
