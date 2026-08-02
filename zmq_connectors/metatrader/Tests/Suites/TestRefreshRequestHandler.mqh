//+------------------------------------------------------------------+
//|                  Tests/Suites/TestRefreshRequestHandler.mqh      |
//|  Suite: RefreshRequestHandler — instrument validation and        |
//|  history-provider invocation with payload/default days.          |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"

#include "../../Commands/RefreshRequestHandler.mqh"

//+------------------------------------------------------------------+
//| RecordingHistoryProvider — IHistoryProvider fake local to this   |
//|  suite; records each SendHistory call.                           |
//+------------------------------------------------------------------+
class RecordingHistoryProvider : public IHistoryProvider
{
private:
   int    m_calls;
   int    m_lastDays;
   string m_lastSymbol;

public:
   RecordingHistoryProvider()
   {
      m_calls = 0;
      m_lastDays = -1;
      m_lastSymbol = "";
   }

   int    CallCount()  { return m_calls; }
   int    LastDays()   { return m_lastDays; }
   string LastSymbol() { return m_lastSymbol; }

   void SendHistory(int days = 0, string symbol = "") override
   {
      m_calls++;
      m_lastDays = days;
      m_lastSymbol = symbol;
   }
};

//+------------------------------------------------------------------+
//| _MakeRefreshEnv — {"msg_type":"refresh_request","payload":<json>}|
//+------------------------------------------------------------------+
MessageEnvelope *_MakeRefreshEnv(string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + MT_REFRESH_REQUEST + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunRefreshRequestHandlerTests                                    |
//+------------------------------------------------------------------+
void RunRefreshRequestHandlerTests()
{
   //--- CanHandle claims only "refresh_request"
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
      RecordingHistoryProvider history;
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), GetPointer(history), apis);

      AssertTrue(handler.CanHandle(MT_REFRESH_REQUEST), "Refresh: CanHandle refresh_request");
      AssertFalse(handler.CanHandle(MT_ORDER_OPEN), "Refresh: CanHandle rejects order_open");
      AssertFalse(handler.CanHandle(MT_SUBSCRIBE), "Refresh: CanHandle rejects subscribe");
      delete apis;
   }

   //--- Missing instrument → false + refresh_failed error, provider idle
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
      RecordingHistoryProvider history;
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), GetPointer(history), apis);

      MessageEnvelope *env = _MakeRefreshEnv("{\"days\":7}");
      AssertFalse(handler.Handle(env), "Refresh: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "Refresh: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "refresh_failed"), "Refresh: error type recorded");
      AssertTrue(network.PayloadAtContains(errIdx, "instrument is required"), "Refresh: error message recorded");
      AssertEqualLong(0, history.CallCount(), "Refresh: provider not called without instrument");
      delete env;
      delete apis;
   }

   //--- Unknown symbol → false + refresh_failed error, provider idle
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
      RecordingHistoryProvider history;
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), GetPointer(history), apis);

      MessageEnvelope *env = _MakeRefreshEnv("{\"instrument\":\"NOPE\",\"days\":7}");
      AssertFalse(handler.Handle(env), "Refresh: unknown symbol returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "Refresh: error sent for unknown symbol");
      AssertTrue(network.PayloadAtContains(errIdx, "unknown symbol: NOPE"), "Refresh: unknown-symbol message recorded");
      AssertEqualLong(0, history.CallCount(), "Refresh: provider not called for unknown symbol");
      delete env;
      delete apis;
   }

   //--- Valid request → provider invoked with payload days + symbol
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
      RecordingHistoryProvider history;
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), GetPointer(history), apis);

      MessageEnvelope *env = _MakeRefreshEnv("{\"instrument\":\"EURUSD\",\"days\":7}");
      AssertTrue(handler.Handle(env), "Refresh: valid request returns true");
      AssertEqualLong(1, history.CallCount(), "Refresh: provider called once");
      AssertEqualLong(7, history.LastDays(), "Refresh: payload days forwarded");
      AssertEqualString("EURUSD", history.LastSymbol(), "Refresh: instrument forwarded");
      AssertEqualLong(0, network.SentCount(), "Refresh: success sends nothing itself");
      delete env;
      delete apis;
   }

   //--- Missing days → provider receives 0 (config default applies downstream)
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
      RecordingHistoryProvider history;
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), GetPointer(history), apis);

      MessageEnvelope *env = _MakeRefreshEnv("{\"instrument\":\"GBPUSD\"}");
      AssertTrue(handler.Handle(env), "Refresh: dayless request returns true");
      AssertEqualLong(1, history.CallCount(), "Refresh: provider called for dayless request");
      AssertEqualLong(0, history.LastDays(), "Refresh: missing days forwarded as zero");
      AssertEqualString("GBPUSD", history.LastSymbol(), "Refresh: dayless instrument forwarded");
      delete env;
      delete apis;
   }

   //--- NULL history provider → still true, warning logged
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
      RefreshRequestHandler handler(GetPointer(network), GetPointer(logger), NULL, apis);

      MessageEnvelope *env = _MakeRefreshEnv("{\"instrument\":\"EURUSD\",\"days\":3}");
      AssertTrue(handler.Handle(env), "Refresh: NULL provider returns true");
      AssertTrue(logger.Contains("no history provider available"), "Refresh: NULL provider warning logged");
      AssertEqualLong(0, network.SentCount(MT_ERROR), "Refresh: NULL provider sends no error");
      delete env;
      delete apis;
   }
}
//+------------------------------------------------------------------+
