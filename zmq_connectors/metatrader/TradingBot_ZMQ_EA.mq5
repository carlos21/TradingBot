//+------------------------------------------------------------------+
//|                                      TradingBot_ZMQ_EA.mq5       |
//|                        ZeroMQ Connector for MetaTrader 5         |
//|                                                                  |
//|  This EA connects to Python TradingBot via ZeroMQ for            |
//|  high-frequency trading communication.                           |
//|                                                                  |
//|  Installation:                                                   |
//|  1. Install ZMQ library for MT5 (see README)                     |
//|  2. Copy this file to MQL5/Experts/                             |
//|  3. Compile and run on chart                                     |
//+------------------------------------------------------------------+

#property copyright "TradingBot"
#property link      ""
#property version   "1.00"

#include <Zmq/Zmq.mqh>

//--- Input Parameters
input string   Host = "127.0.0.1";
input int      MarketDataPort = 5555;   // Python PUB - we SUB to this
input int      CommandPort = 5556;      // Python PUSH - we PULL from this
input int      QueryPort = 5557;        // Python REQ - we REP to this
input int      HeartbeatPort = 5558;    // Heartbeat PUB
input string   TradingSymbol = "";
input int      HistoryDays = 30;

//--- Global Variables
ZmqContext Context;
ZmqSocket MarketPub;      // PUB: Send ticks/bars to Python
ZmqSocket CommandPull;    // PULL: Receive commands from Python
ZmqSocket QueryRep;       // REP: Handle queries from Python
ZmqSocket HeartbeatPub;   // PUB: Send heartbeats

bool IsConnected = false;
long LastHeartbeat = 0;
int SequenceNumber = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Use current symbol if not specified
   string symbol = TradingSymbol;
   if(StringLen(symbol) == 0)
      symbol = Symbol();
   
   Print("=== TradingBot ZMQ Connector Starting ===");
   Print("Symbol: ", symbol);
   Print("Python Host: ", Host);
   
   // Create context
   Context = new ZmqContext();
   
   // Create sockets
   if(!InitializeSockets())
   {
      Print("ERROR: Failed to initialize ZMQ sockets");
      return INIT_FAILED;
   }
   
   IsConnected = true;
   LastHeartbeat = GetTickCount();
   
   // Send connect message
   SendConnect(symbol);
   
   // Send historical data
   SendHistory();
   
   Print("=== ZMQ Connector Ready ===");
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("=== ZMQ Connector Stopping ===");
   
   IsConnected = false;
   
   // Close sockets
   delete MarketPub;
   delete CommandPull;
   delete QueryRep;
   delete HeartbeatPub;
   delete Context;
   
   Print("=== ZMQ Connector Stopped ===");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsConnected) return;
   
   MqlTick tick;
   if(!SymbolInfoTick(Symbol(), tick)) return;
   
   // Send tick to Python
   SendTick(tick);
   
   // Process any pending commands (non-blocking)
   ProcessCommands();
   
   // Send heartbeat every 5 seconds
   if(GetTickCount() - LastHeartbeat > 5000)
   {
      SendHeartbeat();
      LastHeartbeat = GetTickCount();
   }
}

//+------------------------------------------------------------------+
//| Timer function                                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Additional command processing on timer
   ProcessCommands();
   ProcessQueries();
}

//+------------------------------------------------------------------+
//| Initialize ZMQ sockets                                           |
//+------------------------------------------------------------------+
bool InitializeSockets()
{
   // PUB socket: Send market data to Python
   MarketPub = new ZmqSocket(Context, ZMQ_PUB);
   if(!MarketPub.connect(StringFormat("tcp://%s:%d", Host, MarketDataPort)))
   {
      Print("ERROR: Failed to connect market data PUB");
      return false;
   }
   
   // PULL socket: Receive commands from Python
   CommandPull = new ZmqSocket(Context, ZMQ_PULL);
   if(!CommandPull.connect(StringFormat("tcp://%s:%d", Host, CommandPort)))
   {
      Print("ERROR: Failed to connect command PULL");
      return false;
   }
   
   // REP socket: Handle queries
   QueryRep = new ZmqSocket(Context, ZMQ_REP);
   if(!QueryRep.connect(StringFormat("tcp://%s:%d", Host, QueryPort)))
   {
      Print("ERROR: Failed to connect query REP");
      return false;
   }
   
   // PUB socket: Heartbeats
   HeartbeatPub = new ZmqSocket(Context, ZMQ_PUB);
   if(!HeartbeatPub.connect(StringFormat("tcp://%s:%d", Host, HeartbeatPort)))
   {
      Print("ERROR: Failed to connect heartbeat PUB");
      return false;
   }
   
   // Give sockets time to connect
   Sleep(100);
   
   return true;
}

//+------------------------------------------------------------------+
//| Send connect handshake                                           |
//+------------------------------------------------------------------+
void SendConnect(string symbol)
{
   JSONValue* payload = new JSONValue();
   payload["platform"] = new JSONValue("metatrader5");
   payload["version"] = new JSONValue("1.0.0");
   payload["pair"] = new JSONValue(symbol);
   
   string json = CreateEnvelope("connect", payload);
   SendMessage(json);
   
   Print("Sent connect handshake");
}

//+------------------------------------------------------------------+
//| Send tick data                                                   |
//+------------------------------------------------------------------+
void SendTick(MqlTick& tick)
{
   JSONValue* payload = new JSONValue();
   payload["pair"] = new JSONValue(Symbol());
   payload["price"] = new JSONValue(tick.last);
   payload["bid"] = new JSONValue(tick.bid);
   payload["ask"] = new JSONValue(tick.ask);
   payload["volume"] = new JSONValue((long)tick.volume);
   payload["time"] = new JSONValue((long)(tick.time));
   
   string json = CreateEnvelope("tick", payload);
   SendMessage(json);
}

//+------------------------------------------------------------------+
//| Send historical bars                                             |
//+------------------------------------------------------------------+
void SendHistory()
{
   datetime end = TimeCurrent();
   datetime start = end - HistoryDays * 24 * 60 * 60;
   
   MqlRates rates[];
   int copied = CopyRates(Symbol(), PERIOD_M1, start, end, rates);
   
   if(copied <= 0)
   {
      Print("ERROR: Failed to copy historical rates");
      return;
   }
   
   Print("Sending ", copied, " historical bars");
   
   // Send in batches
   int batchSize = 500;
   for(int i = 0; i < copied; i += batchSize)
   {
      JSONArray* bars = new JSONArray();
      int endIdx = MathMin(i + batchSize, copied);
      
      for(int j = i; j < endIdx; j++)
      {
         JSONValue* bar = new JSONValue();
         bar["time"] = new JSONValue((long)rates[j].time);
         bar["open"] = new JSONValue(rates[j].open);
         bar["high"] = new JSONValue(rates[j].high);
         bar["low"] = new JSONValue(rates[j].low);
         bar["close"] = new JSONValue(rates[j].close);
         bar["volume"] = new JSONValue((long)rates[j].tick_volume);
         bar["pair"] = new JSONValue(Symbol());
         bars.Add(bar);
      }
      
      JSONValue* payload = new JSONValue();
      payload["pair"] = new JSONValue(Symbol());
      payload["bars"] = bars;
      payload["days"] = new JSONValue(HistoryDays);
      
      string json = CreateEnvelope("history_batch", payload);
      SendMessage(json);
      
      Sleep(10); // Small delay between batches
   }
   
   // Send history end signal
   string endJson = CreateEnvelope("history_end", new JSONValue());
   SendMessage(endJson);
   
   Print("Historical data sent");
}

//+------------------------------------------------------------------+
//| Send heartbeat                                                   |
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   JSONValue* payload = new JSONValue();
   payload["source"] = new JSONValue("metatrader5");
   payload["status"] = new JSONValue("ok");
   
   string json = CreateEnvelope("heartbeat", payload);
   
   ZmqMsg msg(json);
   HeartbeatPub.send(msg, ZMQ_DONTWAIT);
}

//+------------------------------------------------------------------+
//| Process incoming commands                                        |
//+------------------------------------------------------------------+
void ProcessCommands()
{
   ZmqMsg msg;
   
   // Non-blocking receive
   if(!CommandPull.recv(msg, ZMQ_DONTWAIT))
      return;
   
   string json = msg.getData();
   Print("Received command: ", json);
   
   // Parse and dispatch
   JSONParser parser;
   JSONValue* envelope = parser.parse(json);
   
   if(envelope == NULL)
   {
      Print("ERROR: Failed to parse command JSON");
      return;
   }
   
   string msgType = envelope["msg_type"].getString();
   JSONValue* payload = envelope["payload"].getObject();
   
   if(msgType == "order_open")
      HandleOpenOrder(payload);
   else if(msgType == "order_close")
      HandleCloseOrder(payload);
   else if(msgType == "order_modify")
      HandleModifyOrder(payload);
   else
      Print("Unknown command type: ", msgType);
}

//+------------------------------------------------------------------+
//| Process queries                                                  |
//+------------------------------------------------------------------+
void ProcessQueries()
{
   ZmqMsg msg;
   
   // Non-blocking receive
   if(!QueryRep.recv(msg, ZMQ_DONTWAIT))
      return;
   
   string json = msg.getData();
   Print("Received query: ", json);
   
   // TODO: Handle queries (positions, account info, etc.)
   
   // Send response
   JSONValue* response = new JSONValue();
   response["positions"] = new JSONArray();
   
   string responseJson = CreateEnvelope("position_response", response);
   
   ZmqMsg responseMsg(responseJson);
   QueryRep.send(responseMsg);
}

//+------------------------------------------------------------------+
//| Handle open order command                                        |
//+------------------------------------------------------------------+
void HandleOpenOrder(JSONValue* payload)
{
   string tradeId = payload["trade_id"].getString();
   string direction = payload["direction"].getString();
   double entryPrice = payload["entry_price"].getDouble();
   double stopLoss = payload["stop_loss"].getDouble();
   double takeProfit = payload["take_profit"].getDouble();
   
   Print("OPEN ORDER: ", tradeId, " ", direction, " @ ", entryPrice);
   
   // Determine order type
   ENUM_ORDER_TYPE orderType;
   if(direction == "long")
      orderType = ORDER_TYPE_BUY;
   else
      orderType = ORDER_TYPE_SELL;
   
   // TODO: Implement actual order placement
   // For now, simulate fill
   Sleep(100);
   SendEntryFill(tradeId, entryPrice, stopLoss, takeProfit);
}

//+------------------------------------------------------------------+
//| Handle close order command                                       |
//+------------------------------------------------------------------+
void HandleCloseOrder(JSONValue* payload)
{
   string tradeId = payload["trade_id"].getString();
   
   Print("CLOSE ORDER: ", tradeId);
   
   // TODO: Close position
   
   // Simulate fill
   double closePrice = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   SendExitFill(tradeId, closePrice, "CLOSE");
}

//+------------------------------------------------------------------+
//| Handle modify order command                                      |
//+------------------------------------------------------------------+
void HandleModifyOrder(JSONValue* payload)
{
   string tradeId = payload["trade_id"].getString();
   
   if(payload["stop_loss"] != NULL)
   {
      double newSl = payload["stop_loss"].getDouble();
      Print("MODIFY ORDER: ", tradeId, " new SL=", newSl);
   }
   
   // TODO: Modify position
}

//+------------------------------------------------------------------+
//| Send entry fill notification                                     |
//+------------------------------------------------------------------+
void SendEntryFill(string tradeId, double entryPrice, double sl, double tp)
{
   JSONValue* payload = new JSONValue();
   payload["trade_id"] = new JSONValue(tradeId);
   payload["entry_price"] = new JSONValue(entryPrice);
   payload["stop_loss"] = new JSONValue(sl);
   payload["take_profit"] = new JSONValue(tp);
   
   string json = CreateEnvelope("entry_fill", payload);
   SendMessage(json);
   
   Print("Sent entry fill: ", tradeId, " @ ", entryPrice);
}

//+------------------------------------------------------------------+
//| Send exit fill notification                                      |
//+------------------------------------------------------------------+
void SendExitFill(string tradeId, double exitPrice, string resultType)
{
   JSONValue* payload = new JSONValue();
   payload["trade_id"] = new JSONValue(tradeId);
   payload["exit_price"] = new JSONValue(exitPrice);
   payload["result_type"] = new JSONValue(resultType);
   payload["exit_time"] = new JSONValue((long)TimeCurrent());
   
   string json = CreateEnvelope("exit_fill", payload);
   SendMessage(json);
   
   Print("Sent exit fill: ", tradeId, " @ ", exitPrice, " (", resultType, ")");
}

//+------------------------------------------------------------------+
//| Create message envelope                                          |
//+------------------------------------------------------------------+
string CreateEnvelope(string msgType, JSONValue* payload)
{
   SequenceNumber++;
   
   JSONValue* envelope = new JSONValue();
   envelope["msg_type"] = new JSONValue(msgType);
   envelope["timestamp"] = new JSONValue((double)TimeCurrent());
   envelope["seq_num"] = new JSONValue(SequenceNumber);
   envelope["payload"] = payload;
   
   return envelope.toString();
}

//+------------------------------------------------------------------+
//| Send message via market data socket                              |
//+------------------------------------------------------------------+
void SendMessage(string json)
{
   ZmqMsg msg(json);
   MarketPub.send(msg, ZMQ_DONTWAIT);
}

//+------------------------------------------------------------------+
