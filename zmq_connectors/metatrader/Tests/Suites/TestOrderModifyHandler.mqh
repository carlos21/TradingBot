//+------------------------------------------------------------------+
//|                     Tests/Suites/TestOrderModifyHandler.mqh      |
//|  Suite: OrderModifyHandler — payload validation, simulate mode,  |
//|  SL/TP modification via tracked ticket / comment fallback,       |
//|  keep-current semantics for omitted sides, failure paths.        |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Infrastructure/OrderTracking.mqh"

//--- g_e2eTestRunning is re-declared in TestOrderOpenHandler.mqh,
//--- which TestRunnerEA includes before this suite.

#include "../../Commands/OrderModifyHandler.mqh"

//+------------------------------------------------------------------+
//| _MakeOrderModifyEnv — {"msg_type":"order_modify","payload":<json>}|
//+------------------------------------------------------------------+
MessageEnvelope *_MakeOrderModifyEnv(string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + MT_ORDER_MODIFY + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunOrderModifyHandlerTests                                       |
//+------------------------------------------------------------------+
void RunOrderModifyHandlerTests()
{
   //--- CanHandle claims only "order_modify"
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      AssertTrue(handler.CanHandle(MT_ORDER_MODIFY), "OrderModify: CanHandle order_modify");
      AssertFalse(handler.CanHandle(MT_ORDER_OPEN), "OrderModify: CanHandle rejects order_open");
      AssertFalse(handler.CanHandle(MT_ORDER_CLOSE), "OrderModify: CanHandle rejects order_close");
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      AssertFalse(handler.Handle(NULL), "OrderModify: NULL envelope returns false");
      AssertTrue(logger.Contains("OrderModifyHandler: empty envelope"), "OrderModify: NULL envelope logged");

      MessageEnvelope broken;   //--- root == NULL
      AssertFalse(handler.Handle(GetPointer(broken)), "OrderModify: rootless envelope returns false");
      AssertEqualLong(0, network.SentCount(), "OrderModify: broken envelopes send nothing");
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: missing trade_id returns false");
      AssertTrue(logger.Contains("OrderModifyHandler: missing trade_id"), "OrderModify: missing trade_id logged");
      AssertEqualLong(0, network.SentCount(), "OrderModify: missing trade_id sends nothing");
      delete env;
      delete apis;
   }

   //--- Missing instrument → false + order_modify_failed error
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-noinst\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderModify: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "order_modify_failed"), "OrderModify: error type recorded");
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-badacct\",\"instrument\":\"EURUSD\",\"account\":\"99999999\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: account mismatch returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "account_mismatch"), "OrderModify: account_mismatch type recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderModify: account mismatch sends no trade");
      delete env;
      delete apis;
   }

   //--- Unknown symbol → false + order_modify_failed error
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-nope\",\"instrument\":\"NOPE\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: unknown symbol returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "unknown symbol: NOPE"), "OrderModify: unknown-symbol message recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderModify: unknown symbol sends no trade");
      delete env;
      delete apis;
   }

   //--- Simulate mode: trade_log only, no broker interaction
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
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis, true);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-sim\",\"instrument\":\"EURUSD\",\"account\":\"12345678\","
         "\"stop_loss\":1.09500,\"take_profit\":1.12000}");
      AssertTrue(handler.Handle(env), "OrderModify: simulate returns true");
      AssertEqualLong(0, trade.SendCount(), "OrderModify: simulate sends no broker trade");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(logIdx >= 0, "OrderModify: simulate logs trade_log");
      AssertTrue(network.PayloadAtContains(logIdx, "Simulated modify"), "OrderModify: simulate message recorded");
      AssertEqualLong(0, network.SentCount(MT_ERROR), "OrderModify: simulate sends no error");
      delete env;
      delete apis;
   }

   //--- Modify both SL and TP via the tracked ticket
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
      position.AddPosition(7001, 424242, "T-mod1", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-mod1", 7001, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-mod1\",\"instrument\":\"EURUSD\",\"account\":\"12345678\","
         "\"stop_loss\":1.09500,\"take_profit\":1.12000}");
      AssertTrue(handler.Handle(env), "OrderModify: modify both returns true");
      AssertEqualLong(1, trade.SendCount(), "OrderModify: modify sends one trade");
      AssertEqualLong((long)TRADE_ACTION_SLTP, trade.LastAction(), "OrderModify: request action SLTP");
      AssertEqualLong(7001, (long)trade.LastPosition(), "OrderModify: request targets tracked ticket");
      AssertEqualString("EURUSD", trade.LastSymbol(), "OrderModify: request carries symbol");
      AssertEqualDouble(1.09500, trade.LastStopLoss(), 0.000001, "OrderModify: new SL on request");
      AssertEqualDouble(1.12000, trade.LastTakeProfit(), 0.000001, "OrderModify: new TP on request");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(logIdx >= 0, "OrderModify: success logs trade_log");
      AssertTrue(network.PayloadAtContains(logIdx, "SL/TP changed"), "OrderModify: success message recorded");
      delete env;
      delete apis;
   }

   //--- Only SL given → TP kept from the current position
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
      position.AddPosition(7001, 424242, "T-modsl", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-modsl", 7001, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-modsl\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertTrue(handler.Handle(env), "OrderModify: only-SL returns true");
      AssertEqualDouble(1.09500, trade.LastStopLoss(), 0.000001, "OrderModify: only-SL applies new SL");
      AssertEqualDouble(1.11000, trade.LastTakeProfit(), 0.000001, "OrderModify: only-SL keeps current TP");
      delete env;
      delete apis;
   }

   //--- Only TP given → SL kept from the current position
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
      position.AddPosition(7001, 424242, "T-modtp", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-modtp", 7001, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-modtp\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"take_profit\":1.12000}");
      AssertTrue(handler.Handle(env), "OrderModify: only-TP returns true");
      AssertEqualDouble(1.09000, trade.LastStopLoss(), 0.000001, "OrderModify: only-TP keeps current SL");
      AssertEqualDouble(1.12000, trade.LastTakeProfit(), 0.000001, "OrderModify: only-TP applies new TP");
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
      position.AddPosition(8001, 424242, "T-fb", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      //--- tracker NULL → comment fallback drives the lookup
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), NULL,
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-fb\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertTrue(handler.Handle(env), "OrderModify: comment fallback returns true");
      AssertEqualLong(8001, (long)trade.LastPosition(), "OrderModify: fallback found ticket by comment");
      delete env;
      delete apis;
   }

   //--- No position anywhere → false, no error sent to the app
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
      OrderStateManager tracker;   //--- empty: no ticket anywhere → "no position" path
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-gone\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: vanished position returns false");
      AssertTrue(logger.Contains("no position for trade_id=T-gone"), "OrderModify: vanished position logged");
      AssertEqualLong(0, trade.SendCount(), "OrderModify: vanished position sends no trade");
      AssertEqualLong(0, network.SentCount(), "OrderModify: vanished position sends nothing");
      delete env;
      delete apis;
   }

   //--- SelectByTicket failure → false
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
      tracker.TrackEntry("T-stale", 9999, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-stale\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: unselectable position returns false");
      AssertTrue(logger.Contains("PositionSelectByTicket failed"), "OrderModify: select failure logged");
      AssertEqualLong(0, trade.SendCount(), "OrderModify: unselectable position sends no trade");
      delete env;
      delete apis;
   }

   //--- Broker send failure → false + order_modify_failed error
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
      position.AddPosition(7001, 424242, "T-fail", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-fail", 7001, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      trade.QueueResult(10006, 0, 0, 0);
      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-fail\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: broker failure returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderModify: error sent for broker failure");
      AssertTrue(network.PayloadAtContains(errIdx, "order_modify_failed"), "OrderModify: failure error type recorded");
      AssertTrue(network.PayloadAtContains(errIdx, "OrderSend err="), "OrderModify: failure message recorded");
      delete env;
      delete apis;
   }

   //--- Send ok but non-DONE retcode → false + error; tracker untouched
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
      position.AddPosition(7001, 424242, "T-rej", (long)POSITION_TYPE_BUY, 1.10000, 1.09000, 1.11000, 1.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      tracker.TrackEntry("T-rej", 7001, 50.0, 2.0);
      OrderModifyHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                                 GetPointer(config), apis);

      trade.QueueResult(10025, 0, 0, 0);
      trade.ForceSendReturn(true);
      MessageEnvelope *env = _MakeOrderModifyEnv(
         "{\"trade_id\":\"T-rej\",\"instrument\":\"EURUSD\",\"account\":\"12345678\",\"stop_loss\":1.09500}");
      AssertFalse(handler.Handle(env), "OrderModify: rejection retcode returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "Retcode=10025"), "OrderModify: retcode in error message");
      //--- the SUT never touches pending-modify records
      double newSl = 0, newTp = 0;
      AssertFalse(tracker.TryGetPendingModify("T-rej", newSl, newTp), "OrderModify: no pending-modify recorded");
      delete env;
      delete apis;
   }
}
//+------------------------------------------------------------------+
