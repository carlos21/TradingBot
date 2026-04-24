//+------------------------------------------------------------------+
//|                                     Domain/ValueObjects.mqh      |
//|  Immutable data structures used across all layers.               |
//+------------------------------------------------------------------+
#property strict

#include <JSON/JSON.mqh>

//+------------------------------------------------------------------+
//| ZmqConfiguration — runtime config loaded from JSON file          |
//+------------------------------------------------------------------+
class ZmqConfiguration
{
public:
   string host;
   int    marketPort;
   int    commandPort;
   int    queryPort;
   int    heartbeatPort;
   string pair;
   int    historyDays;
   int    heartbeatSec;
   ulong  magicNumber;
   int    maxTicksPerSecond;
   int    batchSize;
   bool   autoConnectOnStartup;
   bool   autoShowPanel;
   string platformVersion;

   // Default constructor
   ZmqConfiguration()
   {
      host = "127.0.0.1";
      marketPort = 5565;
      commandPort = 5566;
      queryPort = 5567;
      heartbeatPort = 5568;
      pair = "EURUSD";
      historyDays = 1;
      heartbeatSec = 5;
      magicNumber = 424242;
      maxTicksPerSecond = 10;
      batchSize = 500;
      autoConnectOnStartup = true;
      autoShowPanel = true;
      platformVersion = "2.0.0";
   }
};

//+------------------------------------------------------------------+
//| MessageEnvelope — standard JSON envelope wrapper                 |
//|  Keeps the full parsed JSON tree; accessors read on demand.      |
//|  {msg_type, timestamp, seq_num, payload}                         |
//+------------------------------------------------------------------+
class MessageEnvelope
{
public:
   JSONValue *root;

   MessageEnvelope()
   {
      root = NULL;
   }

   ~MessageEnvelope()
   {
      if(root != NULL)
      {
         delete root;
         root = NULL;
      }
   }

   //--- Accessor: msg_type
   string MsgType()
   {
      if(root == NULL || !root.HasKey("msg_type")) return "";
      return root["msg_type"].ToString();
   }

   //--- Accessor: timestamp
   double Timestamp()
   {
      if(root == NULL || !root.HasKey("timestamp")) return 0;
      return root["timestamp"].ToDouble();
   }

   //--- Accessor: seq_num
   long SeqNum()
   {
      if(root == NULL || !root.HasKey("seq_num")) return 0;
      return root["seq_num"].ToInt();
   }

   //--- Accessor: string field from payload (safe, no memory leaks)
   string PayloadString(string key, string defaultValue = "")
   {
      if(root == NULL || !root.HasKey("payload")) return defaultValue;
      if(!root["payload"].HasKey(key)) return defaultValue;
      return root["payload"][key].ToString();
   }

   //--- Accessor: double field from payload (safe)
   double PayloadDouble(string key, double defaultValue = 0.0)
   {
      if(root == NULL || !root.HasKey("payload")) return defaultValue;
      if(!root["payload"].HasKey(key)) return defaultValue;
      return root["payload"][key].ToDouble();
   }

   //--- Accessor: bool field from payload (safe)
   bool PayloadBool(string key, bool defaultValue = false)
   {
      if(root == NULL || !root.HasKey("payload")) return defaultValue;
      if(!root["payload"].HasKey(key)) return defaultValue;
      return root["payload"][key].ToBool();
   }

   //--- Check if payload exists and has a key
   bool PayloadHasKey(string key)
   {
      if(root == NULL || !root.HasKey("payload")) return false;
      return root["payload"].HasKey(key);
   }
};

//+------------------------------------------------------------------+
//| PendingEntryInfo — tracks entry order before fill                |
//+------------------------------------------------------------------+
struct PendingEntryInfo
{
   string tradeId;
   string direction;
   double entryPrice;
   double stopLoss;
   double takeProfit;
   double riskPoints;
   double rrRatio;
   double riskUsd;
   string instrument;

   PendingEntryInfo()
   {
      tradeId = "";
      direction = "";
      entryPrice = 0;
      stopLoss = 0;
      takeProfit = 0;
      riskPoints = 0;
      rrRatio = 1.0;
      riskUsd = 0;
      instrument = "";
   }
};

//+------------------------------------------------------------------+
//| PendingModifyInfo — tracks pending SL/TP modification            |
//+------------------------------------------------------------------+
struct PendingModifyInfo
{
   string tradeId;
   double newStopLoss;
   double newTakeProfit;
   string instrument;

   PendingModifyInfo()
   {
      tradeId = "";
      newStopLoss = 0;
      newTakeProfit = 0;
      instrument = "";
   }
};
