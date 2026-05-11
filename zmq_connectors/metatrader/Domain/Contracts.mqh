//+------------------------------------------------------------------+
//|                                      Domain/Contracts.mqh        |
//|  Abstract interfaces (contracts) for dependency injection.       |
//|  Pure virtual classes = Strategy pattern in MQL5.                |
//+------------------------------------------------------------------+
#property strict

#include <JSON/JSON.mqh>

// Forward declarations
class MessageEnvelope;
class ZmqConfiguration;

//+------------------------------------------------------------------+
//| ILogger — logging abstraction                                    |
//+------------------------------------------------------------------+
class ILogger
{
public:
   virtual ~ILogger() {}

   virtual void Info(string msg) = 0;
   virtual void Warning(string msg) = 0;
   virtual void Error(string msg) = 0;
   virtual void Success(string msg) = 0;
};

//+------------------------------------------------------------------+
//| IMessageSerializer — JSON encode/decode abstraction              |
//+------------------------------------------------------------------+
class IMessageSerializer
{
public:
   virtual ~IMessageSerializer() {}

   virtual string Serialize(JSONValue *root) = 0;
   virtual JSONValue *Deserialize(string json) = 0;
};

//+------------------------------------------------------------------+
//| IRateLimiter — throttling abstraction                            |
//+------------------------------------------------------------------+
class IRateLimiter
{
public:
   virtual ~IRateLimiter() {}

   virtual bool TryAllow() = 0;
   virtual void Reset() = 0;
};

//+------------------------------------------------------------------+
//| ICommandHandler — single command processor (Chain of Responsibility)
//+------------------------------------------------------------------+
class ICommandHandler
{
public:
   virtual ~ICommandHandler() {}

   virtual bool CanHandle(string msgType) = 0;
   virtual bool Handle(MessageEnvelope *envelope) = 0;
};

//+------------------------------------------------------------------+
//| ICommandDispatcher — routes commands to handlers                 |
//+------------------------------------------------------------------+
class ICommandDispatcher
{
public:
   virtual ~ICommandDispatcher() {}

   virtual void Register(ICommandHandler *handler) = 0;
   virtual bool Dispatch(MessageEnvelope *envelope) = 0;
};

//+------------------------------------------------------------------+
//| IOrderTracker — tracks order lifecycle for crash recovery        |
//+------------------------------------------------------------------+
class IOrderTracker
{
public:
   virtual ~IOrderTracker() {}

   virtual void TrackEntry(string tradeId, ulong ticket, double slPoints, double rrRatio) = 0;
   virtual void TrackStopLoss(string tradeId, ulong ticket) = 0;
   virtual void TrackTakeProfit(string tradeId, ulong ticket) = 0;
   virtual void TrackCloseOrder(string tradeId, ulong ticket) = 0;

   virtual bool TryGetEntry(string tradeId, ulong &ticket, double &slPoints, double &rrRatio) = 0;
   virtual bool TryGetStopLoss(string tradeId, ulong &ticket) = 0;
   virtual bool TryGetTakeProfit(string tradeId, ulong &ticket) = 0;
   virtual bool TryGetCloseOrder(string tradeId, ulong &ticket) = 0;

   virtual bool TryGetTradeIdForTicket(ulong ticket, string &tradeId) = 0;
   virtual void RemoveTrade(string tradeId) = 0;

   virtual void SetPendingModify(string tradeId, double newSl, double newTp) = 0;
   virtual bool TryGetPendingModify(string tradeId, double &newSl, double &newTp) = 0;
   virtual void RemovePendingModify(string tradeId) = 0;

   virtual void Clear() = 0;
   virtual int GetActiveCount() = 0;
   virtual void GetActiveTradeIds(string &tradeIds[]) = 0;
};

//+------------------------------------------------------------------+
//| IHistoryProvider — abstraction for history refresh               |
//+------------------------------------------------------------------+
class IHistoryProvider
{
public:
   virtual ~IHistoryProvider() {}
   virtual void SendHistory() = 0;
};

//+------------------------------------------------------------------+
//| IZmqNetwork — ZeroMQ send/receive abstraction                    |
//+------------------------------------------------------------------+
class IZmqNetwork
{
public:
   virtual ~IZmqNetwork() {}

   // Lifecycle
   virtual bool Start() = 0;
   virtual void Dispose() = 0;

   // Send methods (Platform → Python)
   virtual void SendTick(string pair, double price, long volume, datetime tickTime) = 0;
   virtual void SendBar(string pair, datetime barTime, double open, double high, double low, double close, long volume, bool isPartial) = 0;
   virtual void SendHistoryBatch(string pair, JSONValue *barsArray, int days) = 0;
   virtual void SendHistoryEnd(string pair) = 0;
   virtual void SendEntryFill(string tradeId, double entryPrice, double stopLoss, double takeProfit) = 0;
   virtual void SendExitFill(string tradeId, double exitPrice, string resultType) = 0;
   virtual void SendTradeLog(string tradeId, string eventType, string message) = 0;
   virtual void SendError(string source, string errorType, string message) = 0;
   virtual void SendHeartbeat(string source, string status) = 0;
   virtual void SendConnect(string platform, string version, string account, string pair) = 0;
   virtual void SendCommandAck(string commandType, long seqNum, bool success, string tradeId, string message) = 0;
   virtual void SendPositionSync(JSONValue *positionsArray, JSONValue *untrackedArray) = 0;
   virtual void SendTestStart(string scenario, double entryPrice, double riskPoints, double rrRatio) = 0;
   virtual void SendTestResult(string scenario, bool passed, string tradeId, string message) = 0;

   // Receive methods (Python → Platform)
   virtual MessageEnvelope *ReceiveCommand(int timeoutMs) = 0;

   // Query methods (REQ/REP)
   virtual string QueryConfig(string key, int timeoutMs) = 0;
   virtual bool SendTestPingWithResponse(int timeoutMs) = 0;
};
