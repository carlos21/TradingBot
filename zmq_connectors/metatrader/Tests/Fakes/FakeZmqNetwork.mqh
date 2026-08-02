//+------------------------------------------------------------------+
//|                                Tests/Fakes/FakeZmqNetwork.mqh    |
//|  Fake IZmqNetwork — records every sent envelope (msg_type +      |
//|  payload string) and replays scripted lifecycle/query results.   |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/Contracts.mqh"
#include "../../Domain/MessageTypes.mqh"
#include "../../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| FakeZmqNetwork — in-memory IZmqNetwork for tests                 |
//+------------------------------------------------------------------+
class FakeZmqNetwork : public IZmqNetwork
{
private:
   string m_msgTypes[];
   string m_payloads[];

   bool   m_startResult;
   bool   m_pingResults[];    // queue for SendTestPingWithResponse
   int    m_pingIndex;
   string m_configResponse;   // canned QueryConfig response
   int    m_restartCount;
   int    m_disposeCount;
   int    m_recreateCount;
   bool   m_started;

   MessageEnvelope *m_commands[];  // queue for ReceiveCommand

   void _Record(string msgType, string payload)
   {
      int n = ArraySize(m_msgTypes);
      ArrayResize(m_msgTypes, n + 1);
      ArrayResize(m_payloads, n + 1);
      m_msgTypes[n] = msgType;
      m_payloads[n] = payload;
   }

public:
   FakeZmqNetwork()
   {
      m_startResult = true;
      m_pingIndex = 0;
      m_configResponse = "";
      m_restartCount = 0;
      m_disposeCount = 0;
      m_recreateCount = 0;
      m_started = false;
   }

   ~FakeZmqNetwork()
   {
      //--- Envelopes queued via QueueCommand but never received are
      //--- owned by the fake; free them to avoid leaks.
      for(int i = 0; i < ArraySize(m_commands); i++)
         if(m_commands[i] != NULL)
            delete m_commands[i];
   }

   //--- Test hooks -------------------------------------------------
   void SetStartResult(bool result)     { m_startResult = result; }
   void QueuePingResult(bool result)
   {
      int n = ArraySize(m_pingResults);
      ArrayResize(m_pingResults, n + 1);
      m_pingResults[n] = result;
   }
   void SetConfigResponse(string response) { m_configResponse = response; }
   void QueueCommand(MessageEnvelope *envelope)
   {
      int n = ArraySize(m_commands);
      ArrayResize(m_commands, n + 1);
      m_commands[n] = envelope;
   }
   int  RestartCount()  { return m_restartCount; }
   int  DisposeCount()  { return m_disposeCount; }
   int  RecreateCount() { return m_recreateCount; }
   bool IsStarted()     { return m_started; }

   //--- Recorded-send inspection -----------------------------------
   int SentCount() { return ArraySize(m_msgTypes); }

   int SentCount(string msgType)
   {
      int count = 0;
      for(int i = 0; i < ArraySize(m_msgTypes); i++)
         if(m_msgTypes[i] == msgType)
            count++;
      return count;
   }

   string LastMsgType()
   {
      int n = ArraySize(m_msgTypes);
      return n > 0 ? m_msgTypes[n - 1] : "";
   }

   string MsgTypeAt(int index)
   {
      if(index < 0 || index >= ArraySize(m_msgTypes)) return "";
      return m_msgTypes[index];
   }

   string PayloadAt(int index)
   {
      if(index < 0 || index >= ArraySize(m_payloads)) return "";
      return m_payloads[index];
   }

   bool PayloadAtContains(int index, string substr)
   {
      return StringFind(PayloadAt(index), substr) >= 0;
   }

   int FindByMsgType(string msgType)
   {
      for(int i = 0; i < ArraySize(m_msgTypes); i++)
         if(m_msgTypes[i] == msgType)
            return i;
      return -1;
   }

   void ClearSent()
   {
      ArrayResize(m_msgTypes, 0);
      ArrayResize(m_payloads, 0);
   }

   //--- IZmqNetwork: lifecycle -------------------------------------
   bool Start() override
   {
      m_started = m_startResult;
      return m_startResult;
   }

   void Dispose() override
   {
      m_disposeCount++;
      m_started = false;
   }

   bool Restart() override
   {
      m_restartCount++;
      Dispose();
      return Start();
   }

   void RecreateRequestSocket() override
   {
      m_recreateCount++;
   }

   //--- IZmqNetwork: send methods ----------------------------------
   void SendTick(string pair, double price, long volume, datetime tickTime) override
   {
      _Record(MT_TICK, StringFormat("pair=%s;price=%f;volume=%I64d;time=%I64d",
                                    pair, price, volume, (long)tickTime));
   }

   void SendBar(string pair, datetime barTime, double open, double high,
                double low, double close, long volume, bool isPartial) override
   {
      _Record(isPartial ? MT_PARTIAL : MT_BAR,
              StringFormat("pair=%s;time=%I64d;open=%f;high=%f;low=%f;close=%f;volume=%I64d",
                           pair, (long)barTime, open, high, low, close, volume));
   }

   void SendHistoryBatch(string pair, JSONValue *barsArray, int days) override
   {
      _Record(MT_HISTORY_BATCH, StringFormat("pair=%s;days=%d;bars=<json>", pair, days));
   }

   void SendRawHistoryBatch(string pair, string barsJson, int days) override
   {
      _Record(MT_HISTORY_BATCH, StringFormat("pair=%s;days=%d;bars=%s", pair, days, barsJson));
   }

   void SendHistoryEnd(string pair) override
   {
      _Record(MT_HISTORY_END, "pair=" + pair);
   }

   void SendRefreshStart(string pair) override
   {
      _Record(MT_REFRESH_START, "pair=" + pair);
   }

   void SendEntryFill(string tradeId, double entryPrice, double stopLoss,
                      double takeProfit, string account = "",
                      double quantity = 0, double accountBalance = 0) override
   {
      _Record(MT_ENTRY_FILL,
              StringFormat("trade_id=%s;entry_price=%f;sl=%f;tp=%f;account=%s;quantity=%f;balance=%f",
                           tradeId, entryPrice, stopLoss, takeProfit,
                           account, quantity, accountBalance));
   }

   void SendExitFill(string tradeId, double exitPrice, string resultType,
                     long exitTime = 0, string account = "",
                     double realizedPnl = 0, double commission = 0,
                     double accountBalance = 0) override
   {
      _Record(MT_EXIT_FILL,
              StringFormat("trade_id=%s;exit_price=%f;result=%s;exit_time=%I64d;account=%s;pnl=%f;commission=%f;balance=%f",
                           tradeId, exitPrice, resultType, exitTime,
                           account, realizedPnl, commission, accountBalance));
   }

   void SendOrderRejected(string tradeId, string reason) override
   {
      _Record(MT_ORDER_REJECTED,
              StringFormat("trade_id=%s;reason=%s", tradeId, reason));
   }

   void SendTradeLog(string tradeId, string eventType, string message) override
   {
      _Record(MT_TRADE_LOG,
              StringFormat("trade_id=%s;event=%s;message=%s", tradeId, eventType, message));
   }

   void SendError(string source, string errorType, string message) override
   {
      _Record(MT_ERROR,
              StringFormat("source=%s;error_type=%s;message=%s", source, errorType, message));
   }

   void SendHeartbeat(string source, string status) override
   {
      _Record(MT_HEARTBEAT, StringFormat("source=%s;status=%s", source, status));
   }

   void SendConnect(string platform, string version, string pair) override
   {
      _Record(MT_CONNECT,
              StringFormat("platform=%s;version=%s;pair=%s", platform, version, pair));
   }

   void SendCommandAck(string commandType, long seqNum, bool success,
                       string tradeId, string message) override
   {
      _Record(MT_COMMAND_ACK,
              StringFormat("command=%s;seq=%I64d;success=%s;trade_id=%s;message=%s",
                           commandType, seqNum, success ? "true" : "false",
                           tradeId, message));
   }

   void SendPositionSync(JSONValue *positionsArray, JSONValue *untrackedArray,
                         int count = 0) override
   {
      //--- Mirror the real ZmqNetwork payload shape so tests can assert
      //--- individual fields; the payload tree (arrays included) is
      //--- deleted here, matching the real network's ownership semantics.
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["positions"] = positionsArray;
      payload["count"]     = new JSONValue((long)count);
      payload["untracked_orders"] = untrackedArray;
      payload["source"]    = new JSONValue("metatrader5");
      payload["is_source_of_truth"] = new JSONValue(true);
      _Record(MT_POSITION_SYNC, payload.Serialize());
      delete payload;
   }

   void SendTestStart(string scenario, double entryPrice, double riskPoints,
                      double rrRatio) override
   {
      _Record(MT_TEST_START,
              StringFormat("scenario=%s;entry_price=%f;risk_points=%f;rr=%f",
                           scenario, entryPrice, riskPoints, rrRatio));
   }

   void SendTestResult(string scenario, bool passed, string tradeId,
                       string message) override
   {
      _Record(MT_TEST_RESULT,
              StringFormat("scenario=%s;passed=%s;trade_id=%s;message=%s",
                           scenario, passed ? "true" : "false", tradeId, message));
   }

   //--- IZmqNetwork: receive ---------------------------------------
   MessageEnvelope *ReceiveCommand(int timeoutMs) override
   {
      if(ArraySize(m_commands) == 0)
         return NULL;
      MessageEnvelope *envelope = m_commands[0];
      //--- Pop the head; ownership transfers to the caller.
      for(int i = 0; i < ArraySize(m_commands) - 1; i++)
         m_commands[i] = m_commands[i + 1];
      ArrayResize(m_commands, ArraySize(m_commands) - 1);
      return envelope;
   }

   //--- IZmqNetwork: queries ---------------------------------------
   string QueryConfig(string key, int timeoutMs) override
   {
      _Record(MT_CONFIG_QUERY, "key=" + key);
      return m_configResponse;
   }

   bool SendTestPingWithResponse(int timeoutMs) override
   {
      _Record(MT_TEST_PING, "");
      if(m_pingIndex < ArraySize(m_pingResults))
         return m_pingResults[m_pingIndex++];
      return true; //--- default: pong received
   }
};
