//+------------------------------------------------------------------+
//|                                  Application/ZmqNetwork.mqh      |
//|  4-socket ZMQ manager. Facade pattern hiding ZMQ complexity.     |
//|  Python binds all sockets; MT connects to them.                  |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include <Zmq/Zmq.mqh>
#include <JSON/JSON.mqh>

//+------------------------------------------------------------------+
//| ZmqNetwork — manages PUB, PULL, REQ, PUB sockets                 |
//+------------------------------------------------------------------+
class ZmqNetwork : public IZmqNetwork
{
private:
   ZmqConfiguration   *m_cfg;
   IMessageSerializer *m_serializer;
   ILogger            *m_logger;

   Context  m_context;
   Socket  *m_marketSocket;     // PUB → Python SUB
   Socket  *m_commandSocket;    // PULL ← Python PUSH
   Socket  *m_querySocket;      // REQ → Python REP
   Socket  *m_heartbeatSocket;  // PUB → Python SUB

   bool     m_disposed;
   long     m_seqNum;

public:
   ZmqNetwork(ZmqConfiguration *cfg, IMessageSerializer *serializer, ILogger *logger)
   {
      m_cfg = cfg;
      m_serializer = serializer;
      m_logger = logger;
      m_disposed = false;
      m_seqNum = 0;
      m_marketSocket = NULL;
      m_commandSocket = NULL;
      m_querySocket = NULL;
      m_heartbeatSocket = NULL;
   }

   ~ZmqNetwork()
   {
      Dispose();
   }

   //--- IZmqNetwork implementation

   bool Start() override
   {
      m_marketSocket    = new Socket(m_context, ZMQ_PUB);
      m_commandSocket   = new Socket(m_context, ZMQ_PULL);
      m_querySocket     = new Socket(m_context, ZMQ_REQ);
      m_heartbeatSocket = new Socket(m_context, ZMQ_PUB);

      string marketAddr    = StringFormat("tcp://%s:%d", m_cfg.host, m_cfg.marketPort);
      string commandAddr   = StringFormat("tcp://%s:%d", m_cfg.host, m_cfg.commandPort);
      string queryAddr     = StringFormat("tcp://%s:%d", m_cfg.host, m_cfg.queryPort);
      string heartbeatAddr = StringFormat("tcp://%s:%d", m_cfg.host, m_cfg.heartbeatPort);

      m_marketSocket.connect(marketAddr);
      m_commandSocket.connect(commandAddr);
      m_querySocket.connect(queryAddr);
      m_heartbeatSocket.connect(heartbeatAddr);

      // Slow-joiner protection for PUB sockets
      Sleep(300);

      if(m_logger != NULL)
      {
         m_logger.Info("ZMQ connected:");
         m_logger.Info("  Market: " + marketAddr);
         m_logger.Info("  Commands: " + commandAddr);
         m_logger.Info("  Queries: " + queryAddr);
         m_logger.Info("  Heartbeat: " + heartbeatAddr);
      }
      return true;
   }

   void Dispose() override
   {
      if(m_disposed) return;
      m_disposed = true;

      if(m_marketSocket != NULL)    { delete m_marketSocket; m_marketSocket = NULL; }
      if(m_commandSocket != NULL)   { delete m_commandSocket; m_commandSocket = NULL; }
      if(m_querySocket != NULL)     { delete m_querySocket; m_querySocket = NULL; }
      if(m_heartbeatSocket != NULL) { delete m_heartbeatSocket; m_heartbeatSocket = NULL; }
      // m_context is a value type; its destructor runs automatically
      // when ZmqNetwork is destroyed, after all sockets are closed
   }

   //--- Send helpers

   void SendTick(string pair, double price, long volume, datetime tickTime) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["pair"]   = new JSONValue(pair);
      payload["price"]  = new JSONValue(price);
      payload["volume"] = new JSONValue((long)volume);
      payload["time"]   = new JSONValue((long)tickTime);
      SendEnvelope(MT_TICK, payload, m_marketSocket);
   }

   void SendBar(string pair, datetime barTime, double open, double high, double low, double close, long volume, bool isPartial) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["pair"]   = new JSONValue(pair);
      payload["time"]   = new JSONValue((long)barTime);
      payload["open"]   = new JSONValue(open);
      payload["high"]   = new JSONValue(high);
      payload["low"]    = new JSONValue(low);
      payload["close"]  = new JSONValue(close);
      payload["volume"] = new JSONValue((long)volume);

      string msgType = isPartial ? MT_PARTIAL : MT_BAR;
      SendEnvelope(msgType, payload, m_marketSocket);
   }

   void SendHistoryBatch(string pair, JSONValue *barsArray, int days) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["pair"] = new JSONValue(pair);
      payload["days"] = new JSONValue(days);
      payload["bars"] = barsArray;
      SendEnvelope(MT_HISTORY_BATCH, payload, m_marketSocket);
      // NOTE: barsArray is deleted as part of the payload tree — caller must NOT delete it
   }

   void SendHistoryEnd(string pair) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["pair"] = new JSONValue(pair);
      SendEnvelope(MT_HISTORY_END, payload, m_marketSocket);
   }

   void SendEntryFill(string tradeId, double entryPrice, double stopLoss, double takeProfit) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["trade_id"]    = new JSONValue(tradeId);
      payload["entry_price"] = new JSONValue(entryPrice);
      payload["stop_loss"]   = new JSONValue(stopLoss);
      payload["take_profit"] = new JSONValue(takeProfit);
      SendEnvelope(MT_ENTRY_FILL, payload, m_marketSocket);
   }

   void SendExitFill(string tradeId, double exitPrice, string resultType) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["trade_id"]    = new JSONValue(tradeId);
      payload["exit_price"]  = new JSONValue(exitPrice);
      payload["result_type"] = new JSONValue(resultType);
      payload["exit_time"]   = new JSONValue((long)TimeCurrent());
      SendEnvelope(MT_EXIT_FILL, payload, m_marketSocket);
   }

   void SendTradeLog(string tradeId, string eventType, string message) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["trade_id"] = new JSONValue(tradeId);
      payload["event"]    = new JSONValue(eventType);
      payload["message"]  = new JSONValue(message);
      SendEnvelope(MT_TRADE_LOG, payload, m_marketSocket);
   }

   void SendError(string source, string errorType, string message) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["source"]     = new JSONValue(source);
      payload["error_type"] = new JSONValue(errorType);
      payload["message"]    = new JSONValue(message);
      SendEnvelope(MT_ERROR, payload, m_marketSocket);
   }

   void SendHeartbeat(string source, string status) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["source"] = new JSONValue(source);
      payload["status"] = new JSONValue(status);
      payload["time"]   = new JSONValue((long)TimeCurrent());
      SendEnvelope(MT_HEARTBEAT, payload, m_heartbeatSocket);
   }

   void SendConnect(string platform, string version, string account, string pair) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["platform"] = new JSONValue(platform);
      payload["version"]  = new JSONValue(version);
      payload["account"]  = new JSONValue(account);
      payload["pair"]     = new JSONValue(pair);
      SendEnvelope(MT_CONNECT, payload, m_marketSocket);
   }

   void SendCommandAck(string commandType, long seqNum, bool success, string tradeId, string message) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["command_type"] = new JSONValue(commandType);
      payload["seq_num"]      = new JSONValue((long)seqNum);
      payload["success"]      = new JSONValue(success);
      if(StringLen(tradeId) > 0)
         payload["trade_id"]  = new JSONValue(tradeId);
      if(StringLen(message) > 0)
         payload["message"]   = new JSONValue(message);
      payload["timestamp"]    = new JSONValue((long)TimeCurrent());
      SendEnvelope(MT_COMMAND_ACK, payload, m_marketSocket);
   }

   void SendPositionSync(JSONValue *positionsArray, JSONValue *untrackedArray) override
   {
      JSONValue *payload = new JSONValue(JSON_OBJECT);
      payload["positions"] = positionsArray;
      payload["untracked"] = untrackedArray;
      payload["source"]    = new JSONValue("metatrader5");
      payload["is_source_of_truth"] = new JSONValue(true);
      SendEnvelope(MT_POSITION_SYNC, payload, m_marketSocket);
      // NOTE: arrays are deleted as part of the payload tree — caller must NOT delete them
   }

   //--- Receive command (non-blocking)
   MessageEnvelope *ReceiveCommand(int timeoutMs) override
   {
      ZmqMsg msg;
      if(!m_commandSocket.recv(msg, ZMQ_DONTWAIT))
         return NULL;

      string json = msg.getData();
      if(StringLen(json) == 0)
         return NULL;

      JSONValue *root = m_serializer.Deserialize(json);
      if(root == NULL)
         return NULL;

      MessageEnvelope *env = new MessageEnvelope();
      env.root = root;
      return env;
   }

   //--- Query config (synchronous REQ/REP)
   string QueryConfig(string key, int timeoutMs) override
   {
      // NOTE: timeoutMs is currently unused — Zmq.mqh recv() does not support timeouts
      JSONValue *reqPayload = new JSONValue(JSON_OBJECT);
      reqPayload["key"] = new JSONValue(key);

      JSONValue *reqRoot = new JSONValue(JSON_OBJECT);
      reqRoot["msg_type"]  = new JSONValue(MT_CONFIG_QUERY);
      reqRoot["timestamp"] = new JSONValue((long)TimeCurrent());
      reqRoot["seq_num"]   = new JSONValue(++m_seqNum);
      reqRoot["payload"]   = reqPayload;

      string reqJson = m_serializer.Serialize(reqRoot);
      delete reqRoot;

      ZmqMsg reqMsg(reqJson);
      if(!m_querySocket.send(reqMsg))
      {
         if(m_logger != NULL)
            m_logger.Warning("QueryConfig send failed");
         return "";
      }

      ZmqMsg repMsg;
      if(!m_querySocket.recv(repMsg))
      {
         if(m_logger != NULL)
            m_logger.Warning("QueryConfig receive failed (timeout?)");
         return "";
      }

      string repJson = repMsg.getData();
      JSONValue *repRoot = m_serializer.Deserialize(repJson);
      if(repRoot == NULL || !repRoot.IsObject())
         return "";

      string value = "";
      if(repRoot.HasKey("payload"))
      {
         if(repRoot["payload"].HasKey(key))
            value = repRoot["payload"][key].ToString();
      }
      delete repRoot;
      return value;
   }

   //--- Test ping/pong (synchronous REQ/REP)
   bool SendTestPingWithResponse(int timeoutMs) override
   {
      // NOTE: timeoutMs is currently unused — Zmq.mqh recv() does not support timeouts
      JSONValue *reqRoot = new JSONValue(JSON_OBJECT);
      reqRoot["msg_type"]  = new JSONValue(MT_TEST_PING);
      reqRoot["timestamp"] = new JSONValue((long)TimeCurrent());
      reqRoot["seq_num"]   = new JSONValue(++m_seqNum);

      string reqJson = m_serializer.Serialize(reqRoot);
      delete reqRoot;

      ZmqMsg reqMsg(reqJson);
      if(!m_querySocket.send(reqMsg))
         return false;

      ZmqMsg repMsg;
      if(!m_querySocket.recv(repMsg))
         return false;

      string repJson = repMsg.getData();
      JSONValue *repRoot = m_serializer.Deserialize(repJson);
      if(repRoot == NULL || !repRoot.IsObject())
         return false;

      bool isPong = false;
      if(repRoot.HasKey("msg_type"))
         isPong = (repRoot["msg_type"].ToString() == MT_TEST_PONG);

      delete repRoot;
      return isPong;
   }

private:
   //--- Internal: wrap payload in envelope and send
   void SendEnvelope(string msgType, JSONValue *payload, Socket *socket)
   {
      if(socket == NULL) return;

      JSONValue *root = new JSONValue(JSON_OBJECT);
      root["msg_type"]  = new JSONValue(msgType);
      root["timestamp"] = new JSONValue((long)TimeCurrent());
      root["seq_num"]   = new JSONValue(++m_seqNum);
      root["payload"]   = payload;

      string json = m_serializer.Serialize(root);
      delete root; // Deletes entire tree including payload

      ZmqMsg msg(json);
      socket.send(msg);
   }
};
