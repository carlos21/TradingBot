//+------------------------------------------------------------------+
//|                     Tests/Suites/TestOrderOpenHandler.mqh        |
//|  Suite: OrderOpenHandler — payload validation matrix, simulate   |
//|  fills, live market entry with dynamic lot sizing, duplicate     |
//|  guard, broker success/failure paths.                            |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Infrastructure/OrderTracking.mqh"

//--- The EA-owned global the order handlers consult for E2E simulate
//--- mode (declared in TradingBotZmqEA.mq5:87). Re-declared here so
//--- the handlers link inside the test harness.
bool g_e2eTestRunning = false;

#include "../../Commands/OrderOpenHandler.mqh"

//+------------------------------------------------------------------+
//| _MakeOrderOpenEnv — {"msg_type":"order_open","payload":<json>}   |
//+------------------------------------------------------------------+
MessageEnvelope *_MakeOrderOpenEnv(string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + MT_ORDER_OPEN + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunOrderOpenHandlerTests                                         |
//+------------------------------------------------------------------+
void RunOrderOpenHandlerTests()
{
   //--- CanHandle claims only "order_open"
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      AssertTrue(handler.CanHandle(MT_ORDER_OPEN), "OrderOpen: CanHandle order_open");
      AssertFalse(handler.CanHandle(MT_ORDER_CLOSE), "OrderOpen: CanHandle rejects order_close");
      AssertFalse(handler.CanHandle(MT_ORDER_MODIFY), "OrderOpen: CanHandle rejects order_modify");
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      AssertFalse(handler.Handle(NULL), "OrderOpen: NULL envelope returns false");
      AssertTrue(logger.Contains("OrderOpenHandler: empty envelope"), "OrderOpen: NULL envelope logged");

      MessageEnvelope broken;   //--- root == NULL
      AssertFalse(handler.Handle(GetPointer(broken)), "OrderOpen: rootless envelope returns false");
      AssertEqualLong(0, network.SentCount(), "OrderOpen: broken envelopes send nothing");
      delete apis;
   }

   //--- Missing trade_id / direction → false, no network traffic
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"direction\":\"long\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: missing trade_id returns false");
      AssertTrue(logger.Contains("missing trade_id or direction"), "OrderOpen: missing trade_id logged");
      AssertEqualLong(0, network.SentCount(), "OrderOpen: missing trade_id sends nothing");
      delete env;

      env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-nodir\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: missing direction returns false");
      AssertEqualLong(0, network.SentCount(), "OrderOpen: missing direction sends nothing");
      delete env;
      delete apis;
   }

   //--- Missing instrument → false + order_open_failed error
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-noinst\",\"direction\":\"long\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderOpen: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "order_open_failed"), "OrderOpen: error type recorded");
      AssertTrue(network.PayloadAtContains(errIdx, "instrument is required"), "OrderOpen: error message recorded");
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-badacct\",\"direction\":\"long\",\"instrument\":\"EURUSD\",\"account\":\"99999999\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: account mismatch returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderOpen: error sent for account mismatch");
      AssertTrue(network.PayloadAtContains(errIdx, "account_mismatch"), "OrderOpen: account_mismatch type recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: account mismatch sends no trade");
      AssertEqualLong(0, network.SentCount(MT_ENTRY_FILL), "OrderOpen: account mismatch sends no fill");
      delete env;
      delete apis;
   }

   //--- Unknown symbol → false + order_open_failed error
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-nope\",\"direction\":\"long\",\"instrument\":\"NOPE\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: unknown symbol returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "OrderOpen: error sent for unknown symbol");
      AssertTrue(network.PayloadAtContains(errIdx, "unknown symbol: NOPE"), "OrderOpen: unknown-symbol message recorded");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: unknown symbol sends no trade");
      delete env;
      delete apis;
   }

   //--- Simulate mode: fake fill derived from risk_points, no broker
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis, true);

      //--- No entry_price → Bid 1.10000; risk_points=50, rr default 1.0
      //--- → SL = 1.10000 - 50*0.00001 = 1.09950, TP = 1.10000 + 50*0.00001 = 1.10050
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-sim1\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_points\":50}");
      AssertTrue(handler.Handle(env), "OrderOpen: simulate open returns true");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: simulate sends no broker trade");
      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(fillIdx >= 0, "OrderOpen: simulate records entry_fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "trade_id=T-sim1"), "OrderOpen: simulate fill carries trade_id");
      AssertTrue(network.PayloadAtContains(fillIdx, "entry_price=1.100000"), "OrderOpen: simulate fill at bid");
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=1.099500"), "OrderOpen: simulate SL from risk_points");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=1.100500"), "OrderOpen: simulate TP from risk*rr");
      AssertTrue(network.PayloadAtContains(fillIdx, "account=12345678"), "OrderOpen: simulate fill echoes account");
      AssertTrue(network.FindByMsgType(MT_TRADE_LOG) >= 0, "OrderOpen: simulate logs trade_log");
      delete env;
      delete apis;
   }

   //--- Simulate mode: explicit entry/sl/tp are echoed as-is
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis, true);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-sim2\",\"direction\":\"short\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"entry_price\":1.23456,\"stop_loss\":1.24000,"
         "\"take_profit\":1.23000,\"risk_points\":50}");
      AssertTrue(handler.Handle(env), "OrderOpen: simulate explicit prices returns true");
      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(network.PayloadAtContains(fillIdx, "entry_price=1.234560"), "OrderOpen: explicit entry price echoed");
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=1.240000"), "OrderOpen: explicit SL echoed");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=1.230000"), "OrderOpen: explicit TP echoed");
      delete env;
      delete apis;
   }

   //--- g_e2eTestRunning forces simulate even without the flag
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis, false);

      g_e2eTestRunning = true;
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-e2e\",\"direction\":\"long\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderOpen: e2e flag forces simulate");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: e2e simulate sends no broker trade");
      AssertTrue(network.FindByMsgType(MT_ENTRY_FILL) >= 0, "OrderOpen: e2e simulate records entry_fill");
      delete env;
      g_e2eTestRunning = false;
      delete apis;
   }

   //--- Live long: lot sizing, request fields, fill from result
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      //--- risk 100 USD / 50 points, tickValue 1.0, tickSize=point
      //--- → volume = 100 / (50 * 1.0) = 2.0
      trade.QueueResult(10009, 555001, 1.10010, 2.0);   //--- TRADE_RETCODE_DONE
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-live1\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50,\"rr_ratio\":2}");
      AssertTrue(handler.Handle(env), "OrderOpen: live long returns true");
      AssertEqualLong(1, trade.SendCount(), "OrderOpen: live long sends one trade");
      AssertEqualLong((long)TRADE_ACTION_DEAL, trade.LastAction(), "OrderOpen: request action DEAL");
      AssertEqualLong((long)ORDER_TYPE_BUY, trade.LastType(), "OrderOpen: long maps to BUY");
      AssertEqualDouble(1.10010, trade.LastPrice(), 0.000001, "OrderOpen: BUY priced at ask");
      AssertEqualDouble(2.0, trade.LastVolume(), 0.0001, "OrderOpen: computed volume 2.0 lots");
      AssertEqualLong(424242, trade.LastMagic(), "OrderOpen: magic from config");
      AssertEqualString("T-live1", trade.LastComment(), "OrderOpen: trade_id as comment");

      //--- Fill derives SL/TP from the actual fill price:
      //--- SL = 1.10010 - 50p = 1.09960, TP = 1.10010 + 100p = 1.10110
      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      AssertTrue(fillIdx >= 0, "OrderOpen: live success sends entry_fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "entry_price=1.100100"), "OrderOpen: fill at broker price");
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=1.099600"), "OrderOpen: live SL from fill price");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=1.101100"), "OrderOpen: live TP from fill price");
      AssertTrue(network.PayloadAtContains(fillIdx, "quantity=2.000000"), "OrderOpen: fill carries volume");
      AssertTrue(network.PayloadAtContains(fillIdx, "balance=100000.000000"), "OrderOpen: fill carries balance");

      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertTrue(tracker.TryGetEntry("T-live1", ticket, slPoints, rrRatio), "OrderOpen: trade tracked");
      AssertEqualLong(555001, (long)ticket, "OrderOpen: tracked ticket is broker order");
      delete env;
      delete apis;
   }

   //--- Live short: SELL at bid, SL above / TP below the fill
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      trade.QueueResult(10009, 555002, 1.10000, 0.5);
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-live2\",\"direction\":\"short\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50,\"rr_ratio\":2}");
      AssertTrue(handler.Handle(env), "OrderOpen: live short returns true");
      AssertEqualLong((long)ORDER_TYPE_SELL, trade.LastType(), "OrderOpen: short maps to SELL");
      AssertEqualDouble(1.10000, trade.LastPrice(), 0.000001, "OrderOpen: SELL priced at bid");

      int fillIdx = network.FindByMsgType(MT_ENTRY_FILL);
      //--- SL = 1.10000 + 50p = 1.10050, TP = 1.10000 - 100p = 1.09900
      AssertTrue(network.PayloadAtContains(fillIdx, "sl=1.100500"), "OrderOpen: short SL above fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "tp=1.099000"), "OrderOpen: short TP below fill");
      AssertTrue(network.PayloadAtContains(fillIdx, "quantity=0.500000"), "OrderOpen: fill uses broker volume");
      delete env;
      delete apis;
   }

   //--- Explicit stop_loss/take_profit go onto the broker request
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      trade.QueueResult(10009, 555003, 1.10010, 1.0);
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-live3\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50,"
         "\"stop_loss\":1.09000,\"take_profit\":1.12000}");
      AssertTrue(handler.Handle(env), "OrderOpen: explicit SL/TP returns true");
      AssertEqualDouble(1.09000, trade.LastStopLoss(), 0.000001, "OrderOpen: request SL from payload");
      AssertEqualDouble(1.12000, trade.LastTakeProfit(), 0.000001, "OrderOpen: request TP from payload");
      delete env;
      delete apis;
   }

   //--- Lot rounding: volume floored to the volume step
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      //--- 100 / (30 * 1.0) = 3.3333 → floored to step 0.01 → 3.33
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-round\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":30}");
      AssertTrue(handler.Handle(env), "OrderOpen: rounded volume returns true");
      AssertEqualDouble(3.33, trade.LastVolume(), 0.0001, "OrderOpen: volume floored to step");
      delete env;
      delete apis;
   }

   //--- Lot clamping: minimum and maximum volume limits
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      //--- 1 / (10000 * 1.0) = 0.0001 → clamped up to volMin 0.01
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-min\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":1,\"risk_points\":10000}");
      AssertTrue(handler.Handle(env), "OrderOpen: min-clamped volume returns true");
      AssertEqualDouble(0.01, trade.LastVolume(), 0.0001, "OrderOpen: volume clamped to minimum");
      delete env;

      //--- 100000 / (1 * 1.0) = 100000 → clamped down to volMax 5.0
      symbols.SetVolumeLimits("EURUSD", 0.01, 5.0, 0.01);
      env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-max\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100000,\"risk_points\":1}");
      AssertTrue(handler.Handle(env), "OrderOpen: max-clamped volume returns true");
      AssertEqualDouble(5.0, trade.LastVolume(), 0.0001, "OrderOpen: volume clamped to maximum");
      delete env;
      delete apis;
   }

   //--- No risk fields → fallback to symbol minimum volume
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-norisk\",\"direction\":\"long\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertTrue(handler.Handle(env), "OrderOpen: riskless open returns true");
      AssertEqualDouble(0.01, trade.LastVolume(), 0.0001, "OrderOpen: riskless volume is symbol minimum");
      delete env;
      delete apis;
   }

   //--- Tick value feeds the sizing math
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
      symbols.SetTickValue("EURUSD", 2.0);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      //--- 50 / (25 ticks * 2.0 USD/tick) = 1.0 lot
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-tickval\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":50,\"risk_points\":25}");
      AssertTrue(handler.Handle(env), "OrderOpen: tick-value sizing returns true");
      AssertEqualDouble(1.0, trade.LastVolume(), 0.0001, "OrderOpen: volume honors tick value");
      delete env;
      delete apis;
   }

   //--- Zero minimum volume → calculated volume is zero → false
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
      symbols.SetVolumeLimits("EURUSD", 0.0, 100.0, 0.01);
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-zerovol\",\"direction\":\"long\",\"instrument\":\"EURUSD\",\"account\":\"12345678\"}");
      AssertFalse(handler.Handle(env), "OrderOpen: zero volume returns false");
      AssertTrue(logger.Contains("calculated volume is zero"), "OrderOpen: zero volume logged");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: zero volume sends no trade");
      delete env;
      delete apis;
   }

   //--- Duplicate trade_id already tracked → ignored, success ack
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
      tracker.TrackEntry("T-dup", 1001, 50.0, 2.0);
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-dup\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50}");
      AssertTrue(handler.Handle(env), "OrderOpen: duplicate returns true (ack-positive)");
      AssertEqualLong(0, trade.SendCount(), "OrderOpen: duplicate sends no trade");
      AssertEqualLong(0, network.SentCount(MT_ENTRY_FILL), "OrderOpen: duplicate sends no fill");
      int logIdx = network.FindByMsgType(MT_TRADE_LOG);
      AssertTrue(logIdx >= 0, "OrderOpen: duplicate logs trade_log");
      AssertTrue(network.PayloadAtContains(logIdx, "Duplicate place_order"), "OrderOpen: duplicate warning logged");
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
      PlatformApis *apis = MakeFakePlatformApis(GetPointer(account), GetPointer(symbols), GetPointer(trade),
                                                GetPointer(marketData), GetPointer(time), GetPointer(position),
                                                GetPointer(dealHistory));
      ZmqConfiguration config;
      OrderStateManager tracker;
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      trade.QueueResult(10006, 0, 0, 0);   //--- Send() mirrors false
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-fail\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50}");
      AssertFalse(handler.Handle(env), "OrderOpen: broker failure returns false");
      int rejIdx = network.FindByMsgType(MT_ORDER_REJECTED);
      AssertTrue(rejIdx >= 0, "OrderOpen: order_rejected recorded");
      AssertTrue(network.PayloadAtContains(rejIdx, "trade_id=T-fail"), "OrderOpen: rejection carries trade_id");
      AssertTrue(network.PayloadAtContains(rejIdx, "OrderSend err="), "OrderOpen: rejection carries reason");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(network.PayloadAtContains(errIdx, "order_open_failed"), "OrderOpen: failure error type recorded");
      delete env;
      delete apis;
   }

   //--- Send ok but rejection retcode → false + order_rejected
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
      OrderOpenHandler handler(GetPointer(network), GetPointer(logger), GetPointer(tracker),
                               GetPointer(config), apis);

      trade.QueueResult(10025, 0, 0, 0);   //--- e.g. TRADE_RETCODE_NO_MONEY-ish
      trade.ForceSendReturn(true);         //--- real OrderSend can do this
      MessageEnvelope *env = _MakeOrderOpenEnv(
         "{\"trade_id\":\"T-rej\",\"direction\":\"long\",\"instrument\":\"EURUSD\","
         "\"account\":\"12345678\",\"risk_usd\":100,\"risk_points\":50}");
      AssertFalse(handler.Handle(env), "OrderOpen: rejection retcode returns false");
      int rejIdx = network.FindByMsgType(MT_ORDER_REJECTED);
      AssertTrue(rejIdx >= 0, "OrderOpen: retcode rejection recorded");
      AssertTrue(network.PayloadAtContains(rejIdx, "Retcode=10025"), "OrderOpen: retcode in rejection reason");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertFalse(tracker.TryGetEntry("T-rej", ticket, slPoints, rrRatio), "OrderOpen: rejected trade not tracked");
      delete env;
      delete apis;
   }
}
//+------------------------------------------------------------------+
