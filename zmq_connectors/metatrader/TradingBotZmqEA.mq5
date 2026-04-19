//+------------------------------------------------------------------+
//|                                      TradingBotZmqEA.mq5         |
//|  ZeroMQ connector for MetaTrader 5 → Python TradingBot           |
//|  Uses the same JSON protocol as the NinjaTrader connector        |
//+------------------------------------------------------------------+
#property copyright "TradingBot"
#property link      ""
#property version   "1.00"
#property strict

#include <Zmq/Zmq.mqh>

//--- Input Parameters
input string   InpHost            = "127.0.0.1";
input int      InpMarketPort      = 5565;    // PUB: market data → Python
input int      InpCommandPort     = 5566;    // PULL: commands ← Python
input int      InpQueryPort       = 5567;    // REP: queries ← Python
input int      InpHeartbeatPort   = 5568;    // PUB: heartbeats → Python
input string   InpPair            = "EURUSD";
input int      InpHistoryDays     = 1;
input int      InpHeartbeatSec    = 5;
input ulong    InpMagicNumber     = 424242;

//--- ZMQ Objects
Context   zmqContext;
Socket    *marketSocket;     // PUB
Socket    *commandSocket;    // PULL
Socket    *querySocket;      // REP
Socket    *heartbeatSocket;  // PUB

//--- State
long      _seqNum = 0;
datetime  _lastHeartbeat = 0;
bool      _connected = false;

//--- Trade tracking: trade_id → ticket
struct TradeMapping { string trade_id; ulong ticket; };
TradeMapping _tradeMap[];

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   zmqContext = new Context();
   
   // Python binds, MT connects
   string marketAddr     = StringFormat("tcp://%s:%d", InpHost, InpMarketPort);
   string commandAddr    = StringFormat("tcp://%s:%d", InpHost, InpCommandPort);
   string queryAddr      = StringFormat("tcp://%s:%d", InpHost, InpQueryPort);
   string heartbeatAddr  = StringFormat("tcp://%s:%d", InpHost, InpHeartbeatPort);
   
   marketSocket    = new Socket(zmqContext, ZMQ_PUB);
   commandSocket   = new Socket(zmqContext, ZMQ_PULL);
   querySocket     = new Socket(zmqContext, ZMQ_REP);
   heartbeatSocket = new Socket(zmqContext, ZMQ_PUB);
   
   marketSocket.connect(marketAddr);
   commandSocket.connect(commandAddr);
   querySocket.connect(queryAddr);
   heartbeatSocket.connect(heartbeatAddr);
   
   // Send initial connect message
   SendConnect();
   
   // Send historical bars
   SendHistory();
   
   Print("[ZMQ] MetaTrader EA connected to ", InpHost);
   Print("[ZMQ] Market: ", marketAddr, " | Commands: ", commandAddr);
   
   _connected = true;
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   _connected = false;
   
   delete marketSocket;
   delete commandSocket;
   delete querySocket;
   delete heartbeatSocket;
   delete zmqContext;
   
   Print("[ZMQ] MetaTrader EA disconnected");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   
   // Send tick
   string json = StringFormat(
      "{\"msg_type\":\"tick\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"pair\":\"%s\",\"price\":%.5f,\"volume\":%I64d,\"time\":%I64d}}",
      TimeCurrent(), ++_seqNum, InpPair, tick.last, tick.volume, tick.time
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
   
   // Send bar on new minute
   static datetime lastBarTime = 0;
   datetime barTime = iTime(_Symbol, PERIOD_M1, 0);
   if(barTime != lastBarTime && lastBarTime != 0)
   {
      SendBar(lastBarTime);
   }
   lastBarTime = barTime;
   
   // Heartbeat
   if(TimeCurrent() - _lastHeartbeat >= InpHeartbeatSec)
   {
      SendHeartbeat();
      _lastHeartbeat = TimeCurrent();
   }
   
   // Poll for commands (non-blocking)
   PollCommands();
   PollQueries();
}

//+------------------------------------------------------------------+
//| Send initial connect message                                     |
//+------------------------------------------------------------------+
void SendConnect()
{
   string json = StringFormat(
      "{\"msg_type\":\"connect\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"platform\":\"metatrader5\",\"version\":\"1.0\",\"pair\":\"%s\",\"account\":\"%I64d\"}}",
      TimeCurrent(), ++_seqNum, InpPair, AccountInfoInteger(ACCOUNT_LOGIN)
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Send heartbeat                                                   |
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   string json = StringFormat(
      "{\"msg_type\":\"heartbeat\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"platform\":\"metatrader5\",\"time\":%I64d}}",
      TimeCurrent(), ++_seqNum, TimeCurrent()
   );
   ZmqMsg msg(json);
   heartbeatSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Send historical bars                                             |
//+------------------------------------------------------------------+
void SendHistory()
{
   datetime end = TimeCurrent();
   datetime start = end - InpHistoryDays * 86400;
   
   int total = CopyRates(_Symbol, PERIOD_M1, start, end, _rates);
   if(total <= 0) return;
   
   // Send in batches of 500
   const int batchSize = 500;
   for(int i = 0; i < total; i += batchSize)
   {
      int endIdx = MathMin(i + batchSize, total);
      string barsJson = "";
      for(int j = i; j < endIdx; j++)
      {
         if(j > i) barsJson += ",";
         barsJson += StringFormat(
            "{\"time\":%I64d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"volume\":%I64d,\"pair\":\"%s\"}",
            _rates[j].time, _rates[j].open, _rates[j].high, _rates[j].low, _rates[j].close, _rates[j].tick_volume, InpPair
         );
      }
      
      string json = StringFormat(
         "{\"msg_type\":\"history_batch\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"pair\":\"%s\",\"days\":%d,\"bars\":[%s]}}",
         TimeCurrent(), ++_seqNum, InpPair, InpHistoryDays, barsJson
      );
      ZmqMsg msg(json);
      marketSocket.send(msg);
   }
   
   // Send history_end
   string json = StringFormat(
      "{\"msg_type\":\"history_end\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"pair\":\"%s\"}}",
      TimeCurrent(), ++_seqNum, InpPair
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

MqlRates _rates[];

//+------------------------------------------------------------------+
//| Send completed bar                                               |
//+------------------------------------------------------------------+
void SendBar(datetime barTime)
{
   int idx = iBarShift(_Symbol, PERIOD_M1, barTime);
   if(idx < 0) return;
   
   MqlRates rate[1];
   if(CopyRates(_Symbol, PERIOD_M1, idx, 1, rate) < 1) return;
   
   string json = StringFormat(
      "{\"msg_type\":\"bar\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"time\":%I64d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"volume\":%I64d,\"pair\":\"%s\"}}",
      TimeCurrent(), ++_seqNum, rate[0].time, rate[0].open, rate[0].high, rate[0].low, rate[0].close, rate[0].tick_volume, InpPair
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Poll for commands (PULL socket)                                  |
//+------------------------------------------------------------------+
void PollCommands()
{
   ZmqMsg msg;
   while(commandSocket.recv(msg, ZMQ_DONTWAIT))
   {
      string json = msg.getData();
      HandleCommand(json);
   }
}

//+------------------------------------------------------------------+
//| Poll for queries (REP socket)                                    |
//+------------------------------------------------------------------+
void PollQueries()
{
   ZmqMsg msg;
   while(querySocket.recv(msg, ZMQ_DONTWAIT))
   {
      string json = msg.getData();
      HandleQuery(json);
   }
}

//+------------------------------------------------------------------+
//| Handle incoming command                                          |
//+------------------------------------------------------------------+
void HandleCommand(string json)
{
   string msgType = ExtractString(json, "msg_type");
   
   if(msgType == "order_open")
   {
      HandleOrderOpen(json);
   }
   else if(msgType == "order_close")
   {
      HandleOrderClose(json);
   }
   else if(msgType == "order_modify")
   {
      HandleOrderModify(json);
   }
   else if(msgType == "refresh_request")
   {
      SendHistory();
   }
}

//+------------------------------------------------------------------+
//| Handle order_open command                                        |
//+------------------------------------------------------------------+
void HandleOrderOpen(string json)
{
   string tradeId = ExtractString(json, "trade_id");
   string direction = ExtractString(json, "direction");
   double entryPrice = ExtractDouble(json, "entry_price");
   double sl = ExtractDouble(json, "stop_loss");
   double tp = ExtractDouble(json, "take_profit");
   
   ENUM_ORDER_TYPE orderType = (direction == "long") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   request.action       = TRADE_ACTION_DEAL;
   request.symbol       = _Symbol;
   request.volume       = 0.01;  // TODO: make configurable
   request.type         = orderType;
   request.price        = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   request.sl           = sl;
   request.tp           = tp;
   request.deviation    = 10;
   request.magic        = InpMagicNumber;
   request.comment      = tradeId;
   
   if(!OrderSend(request, result))
   {
      Print("[ZMQ] OrderSend failed: ", GetLastError());
      SendError("order_open_failed", "OrderSend returned false");
      return;
   }
   
   if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED)
   {
      AddTradeMapping(tradeId, result.order);
      SendEntryFill(tradeId, result.price, sl, tp);
      Print("[ZMQ] Opened ", direction, " ", tradeId, " @ ", result.price);
   }
   else
   {
      Print("[ZMQ] Order failed: ", result.retcode);
      SendError("order_open_failed", StringFormat("Retcode: %d", result.retcode));
   }
}

//+------------------------------------------------------------------+
//| Handle order_close command                                       |
//+------------------------------------------------------------------+
void HandleOrderClose(string json)
{
   string tradeId = ExtractString(json, "trade_id");
   ulong ticket = FindTicketByTradeId(tradeId);
   if(ticket == 0)
   {
      Print("[ZMQ] Close failed: trade_id not found: ", tradeId);
      return;
   }
   
   if(!PositionSelectByTicket(ticket))
   {
      Print("[ZMQ] Close failed: position not open: ", tradeId);
      return;
   }
   
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   request.action   = TRADE_ACTION_DEAL;
   request.position = ticket;
   request.symbol   = _Symbol;
   request.volume   = PositionGetDouble(POSITION_VOLUME);
   request.type     = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price    = (request.type == ORDER_TYPE_SELL) ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   request.deviation = 10;
   request.magic    = InpMagicNumber;
   
   if(!OrderSend(request, result))
   {
      Print("[ZMQ] Close OrderSend failed: ", GetLastError());
      return;
   }
   
   if(result.retcode == TRADE_RETCODE_DONE)
   {
      SendExitFill(tradeId, result.price, "CLOSE");
      RemoveTradeMapping(tradeId);
      Print("[ZMQ] Closed ", tradeId, " @ ", result.price);
   }
}

//+------------------------------------------------------------------+
//| Handle order_modify command                                      |
//+------------------------------------------------------------------+
void HandleOrderModify(string json)
{
   string tradeId = ExtractString(json, "trade_id");
   ulong ticket = FindTicketByTradeId(tradeId);
   if(ticket == 0) return;
   
   if(!PositionSelectByTicket(ticket)) return;
   
   double newSl = ExtractDouble(json, "stop_loss");
   double newTp = ExtractDouble(json, "take_profit");
   
   // Use current SL/TP if not provided
   if(newSl == 0) newSl = PositionGetDouble(POSITION_SL);
   if(newTp == 0) newTp = PositionGetDouble(POSITION_TP);
   
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   request.action    = TRADE_ACTION_SLTP;
   request.position  = ticket;
   request.symbol    = _Symbol;
   request.sl        = newSl;
   request.tp        = newTp;
   
   if(!OrderSend(request, result))
   {
      Print("[ZMQ] Modify failed: ", GetLastError());
      return;
   }
   
   if(result.retcode == TRADE_RETCODE_DONE)
   {
      Print("[ZMQ] Modified ", tradeId, " SL=", newSl, " TP=", newTp);
   }
}

//+------------------------------------------------------------------+
//| Handle query (REQ/REP)                                           |
//+------------------------------------------------------------------+
void HandleQuery(string json)
{
   string msgType = ExtractString(json, "msg_type");
   string response = "{}";
   
   if(msgType == "position_query")
   {
      response = BuildPositionsJson();
   }
   else if(msgType == "test_ping")
   {
      response = StringFormat(
         "{\"msg_type\":\"test_pong\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"timestamp\":%.3f}}",
         TimeCurrent(), ++_seqNum, TimeCurrent()
      );
   }
   
   ZmqMsg respMsg(response);
   querySocket.send(respMsg);
}

//+------------------------------------------------------------------+
//| Build positions JSON for position_query                          |
//+------------------------------------------------------------------+
string BuildPositionsJson()
{
   string positions = "";
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      
      string tradeId = FindTradeIdByTicket(ticket);
      if(tradeId == "") tradeId = StringFormat("mt5_%I64d", ticket);
      
      if(positions != "") positions += ",";
      positions += StringFormat(
         "{\"trade_id\":\"%s\",\"direction\":\"%s\",\"entry_price\":%.5f,\"stop_loss\":%.5f,\"take_profit\":%.5f,\"quantity\":%.2f}",
         tradeId,
         (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "long" : "short",
         PositionGetDouble(POSITION_PRICE_OPEN),
         PositionGetDouble(POSITION_SL),
         PositionGetDouble(POSITION_TP),
         PositionGetDouble(POSITION_VOLUME)
      );
   }
   
   return StringFormat(
      "{\"msg_type\":\"position_response\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"positions\":[%s],\"count\":%d}}",
      TimeCurrent(), ++_seqNum, positions, total
   );
}

//+------------------------------------------------------------------+
//| Send entry_fill event                                            |
//+------------------------------------------------------------------+
void SendEntryFill(string tradeId, double price, double sl, double tp)
{
   string json = StringFormat(
      "{\"msg_type\":\"entry_fill\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"trade_id\":\"%s\",\"entry_price\":%.5f,\"stop_loss\":%.5f,\"take_profit\":%.5f}}",
      TimeCurrent(), ++_seqNum, tradeId, price, sl, tp
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Send exit_fill event                                             |
//+------------------------------------------------------------------+
void SendExitFill(string tradeId, double price, string resultType)
{
   string json = StringFormat(
      "{\"msg_type\":\"exit_fill\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"trade_id\":\"%s\",\"exit_price\":%.5f,\"result_type\":\"%s\"}}",
      TimeCurrent(), ++_seqNum, tradeId, price, resultType
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Send error message                                               |
//+------------------------------------------------------------------+
void SendError(string errorType, string message)
{
   string json = StringFormat(
      "{\"msg_type\":\"error\",\"timestamp\":%.3f,\"seq_num\":%I64d,\"payload\":{\"source\":\"metatrader5\",\"error_type\":\"%s\",\"message\":\"%s\"}}",
      TimeCurrent(), ++_seqNum, errorType, message
   );
   ZmqMsg msg(json);
   marketSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Trade ID ↔ Ticket mapping helpers                                |
//+------------------------------------------------------------------+
void AddTradeMapping(string tradeId, ulong ticket)
{
   int size = ArraySize(_tradeMap);
   ArrayResize(_tradeMap, size + 1);
   _tradeMap[size].trade_id = tradeId;
   _tradeMap[size].ticket = ticket;
}

void RemoveTradeMapping(string tradeId)
{
   int size = ArraySize(_tradeMap);
   for(int i = 0; i < size; i++)
   {
      if(_tradeMap[i].trade_id == tradeId)
      {
         for(int j = i; j < size - 1; j++)
            _tradeMap[j] = _tradeMap[j + 1];
         ArrayResize(_tradeMap, size - 1);
         return;
      }
   }
}

ulong FindTicketByTradeId(string tradeId)
{
   int size = ArraySize(_tradeMap);
   for(int i = 0; i < size; i++)
   {
      if(_tradeMap[i].trade_id == tradeId)
         return _tradeMap[i].ticket;
   }
   return 0;
}

string FindTradeIdByTicket(ulong ticket)
{
   int size = ArraySize(_tradeMap);
   for(int i = 0; i < size; i++)
   {
      if(_tradeMap[i].ticket == ticket)
         return _tradeMap[i].trade_id;
   }
   return "";
}

//+------------------------------------------------------------------+
//| Simple JSON field extractors (best-effort)                       |
//+------------------------------------------------------------------+
string ExtractString(string json, string key)
{
   string pattern = "\"" + key + "\":\"";
   int pos = StringFind(json, pattern);
   if(pos < 0) return "";
   pos += StringLen(pattern);
   int end = StringFind(json, "\"", pos);
   if(end < 0) return "";
   return StringSubstr(json, pos, end - pos);
}

double ExtractDouble(string json, string key)
{
   string pattern = "\"" + key + "\":";
   int pos = StringFind(json, pattern);
   if(pos < 0) return 0.0;
   pos += StringLen(pattern);
   int end = StringFind(json, ",", pos);
   int end2 = StringFind(json, "}", pos);
   if(end2 >= 0 && (end2 < end || end < 0)) end = end2;
   string val = StringSubstr(json, pos, end - pos);
   StringReplace(val, " ", "");
   return StringToDouble(val);
}
