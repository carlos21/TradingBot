//+------------------------------------------------------------------+
//|                                      TradingBotZmqEA.mq5         |
//|  ZeroMQ connector for MetaTrader 5 → Python TradingBot           |
//|  SOLID architecture, clean dependency injection, feature parity  |
//|  with NinjaTrader connector.                                     |
//+------------------------------------------------------------------+
#property copyright "TradingBot"
#property link      ""
#property version   "2.00"
#property strict

#include <Zmq/Zmq.mqh>
#include <JSON/JSON.mqh>

#include "Domain/Contracts.mqh"
#include "Domain/MessageTypes.mqh"
#include "Domain/ValueObjects.mqh"
#include "Domain/TickRateLimiter.mqh"
#include "Infrastructure/ConfigLoader.mqh"
#include "Infrastructure/Logger.mqh"
#include "Infrastructure/Serializers.mqh"
#include "Infrastructure/OrderTracking.mqh"
#include "Application/ZmqNetwork.mqh"
#include "Application/CommandDispatcher.mqh"
#include "Application/E2ETestRunner.mqh"
#include "Application/HistoryProvider.mqh"
#include "Commands/OrderOpenHandler.mqh"
#include "Commands/OrderCloseHandler.mqh"
#include "Commands/OrderModifyHandler.mqh"
#include "Commands/RefreshRequestHandler.mqh"
#include "Commands/TestStartHandler.mqh"

//--- Input Parameters (fallback if config file missing)
input string   InpHost            = "127.0.0.1";
input int      InpMarketPort      = 5565;
input int      InpCommandPort     = 5566;
input int      InpQueryPort       = 5567;
input int      InpHeartbeatPort   = 5568;
input string   InpPair            = "EURUSD";
input int      InpHistoryDays     = 1;
input int      InpHeartbeatSec    = 5;
input ulong    InpMagicNumber     = 424242;

//--- Dependencies (injected)
ZmqConfiguration   *_config;
MetaTraderLogger   *_logger;  // Concrete type for panel access; upcasts to ILogger*
IMessageSerializer *_serializer;
IOrderTracker      *_orderTracker;
IZmqNetwork        *_network;
ICommandDispatcher *_dispatcher;
IRateLimiter       *_tickRateLimiter;
IRateLimiter       *_partialBarRateLimiter;
IHistoryProvider   *_historyProvider;

//--- Command handlers (tracked for cleanup)
OrderOpenHandler     *_handlerOpen;
OrderCloseHandler    *_handlerClose;
OrderModifyHandler   *_handlerModify;
RefreshRequestHandler *_handlerRefresh;
TestStartHandler     *_handlerTest;

//--- State
bool               _connected = false;
long               _seqNum = 0;
datetime           _lastBarTime = 0;
int                _heartbeatCounter = 0;

//--- Stats
long               _ticksSent = 0;
long               _barsSent = 0;
long               _partialBarsSent = 0;
long               _commandsReceived = 0;

//--- Duplicate detection
long               _processedSeqNums[];
const int          MAX_TRACKED_SEQ_NUMS = 1000;

//--- Deal tracking (for OnTrade fill detection)
ulong              _processedDeals[];
const int          MAX_TRACKED_DEALS = 1000;

//--- Pending refresh flag
bool               _pendingHistoryRefresh = false;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // 1. Load configuration (file overrides inputs)
   _config = new ZmqConfiguration();
   ZmqConfiguration fileCfg = ConfigLoader::Load();

   // Use inputs as fallback; file values take precedence
   _config.host = (StringLen(fileCfg.host) > 0) ? fileCfg.host : InpHost;
   _config.marketPort = (fileCfg.marketPort > 0) ? fileCfg.marketPort : InpMarketPort;
   _config.commandPort = (fileCfg.commandPort > 0) ? fileCfg.commandPort : InpCommandPort;
   _config.queryPort = (fileCfg.queryPort > 0) ? fileCfg.queryPort : InpQueryPort;
   _config.heartbeatPort = (fileCfg.heartbeatPort > 0) ? fileCfg.heartbeatPort : InpHeartbeatPort;
   _config.pair = (StringLen(fileCfg.pair) > 0) ? fileCfg.pair : InpPair;
   _config.historyDays = (fileCfg.historyDays > 0) ? fileCfg.historyDays : InpHistoryDays;
   _config.heartbeatSec = (fileCfg.heartbeatSec > 0) ? fileCfg.heartbeatSec : InpHeartbeatSec;
   _config.magicNumber = (fileCfg.magicNumber > 0) ? fileCfg.magicNumber : InpMagicNumber;

   // 2. Create dependencies (Dependency Injection)
   _logger = new MetaTraderLogger("[ZMQ]");
   _serializer = new JsonMessageSerializer(_logger);
   _orderTracker = new OrderStateManager();
   _network = new ZmqNetwork(_config, _serializer, _logger);
   _tickRateLimiter = new TickRateLimiter(_config.maxTicksPerSecond);
   _partialBarRateLimiter = new TickRateLimiter(1); // 1 partial bar/sec
   _historyProvider = new HistoryProvider(_network, _logger, _config, _Symbol);

   // 3. Connect ZMQ
   if(!_network.Start())
   {
      _logger.Error("Failed to start ZMQ network");
      Cleanup();
      return INIT_FAILED;
   }
   _connected = true;

   // 4. Query config from Python (account name, etc.)
   string configuredAccount = _network.QueryConfig("account", 2000);
   if(StringLen(configuredAccount) > 0)
      _logger.Info("Python specified account: " + configuredAccount);
   else
      configuredAccount = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));

   // 5. Send connect handshake
   _network.SendConnect("metatrader5", _config.platformVersion, configuredAccount, _Symbol);
   _logger.Success("Connected to Python TradingBot via ZeroMQ");

   // 6. Restore order tracking from broker (crash recovery)
   RestoreFromBroker();

   // 7. Report positions to Python (source of truth sync)
   ReportPositionsToPython();

   // 8. Send historical data
   _historyProvider.SendHistory();

   // 9. Setup command dispatcher
   _dispatcher = new CommandDispatcher(_logger);

   _handlerOpen = new OrderOpenHandler(_network, _logger, _orderTracker, _config.magicNumber, _Symbol);
   _handlerClose = new OrderCloseHandler(_network, _logger, _orderTracker, _config.magicNumber, _Symbol);
   _handlerModify = new OrderModifyHandler(_network, _logger, _orderTracker, _config.magicNumber, _Symbol);
   _handlerRefresh = new RefreshRequestHandler(_network, _logger, _historyProvider);
   _handlerTest = new TestStartHandler(_network, _logger);

   _dispatcher.Register(_handlerOpen);
   _dispatcher.Register(_handlerClose);
   _dispatcher.Register(_handlerModify);
   _dispatcher.Register(_handlerRefresh);
   _dispatcher.Register(_handlerTest);

   // 10. Start heartbeat timer (1-second granularity)
   EventSetTimer(1);

   _logger.Info("Command dispatcher ready. Handlers: open, close, modify, refresh, test");
   UpdatePanel();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   _connected = false;
   EventKillTimer();
   Sleep(100); // Let in-flight sends drain
   Cleanup();
   Comment(""); // Clear chart comment
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!_connected) return;

   // 1. Send tick (rate-limited)
   SendTickIfAllowed();

   // 2. Send bar on new minute + partial bar
   SendBarIfNew();

   // 3. Poll for commands (non-blocking)
   PollCommands();

   // 4. Handle pending history refresh
   if(_pendingHistoryRefresh)
   {
      _pendingHistoryRefresh = false;
      if(_historyProvider != NULL)
         _historyProvider.SendHistory();
   }

   // 5. Update panel periodically
   static datetime lastPanelUpdate = 0;
   if(TimeCurrent() - lastPanelUpdate >= 1)
   {
      lastPanelUpdate = TimeCurrent();
      UpdatePanel();
   }
}

//+------------------------------------------------------------------+
//| Timer function (heartbeat)                                       |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!_connected) return;

   _heartbeatCounter++;
   if(_heartbeatCounter >= _config.heartbeatSec)
   {
      _heartbeatCounter = 0;
      _network.SendHeartbeat("metatrader5", "ok");
   }
}

//+------------------------------------------------------------------+
//| Trade event function (detect fills)                              |
//+------------------------------------------------------------------+
void OnTrade()
{
   if(!_connected) return;
   ProcessNewDeals();
}

// ═══════════════════════════════════════════════════════════════════
// Market Data
// ═══════════════════════════════════════════════════════════════════

void SendTickIfAllowed()
{
   if(_tickRateLimiter == NULL || !_tickRateLimiter.TryAllow())
      return;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   _network.SendTick(_Symbol, tick.last, tick.volume, tick.time);
   _ticksSent++;
}

void SendBarIfNew()
{
   datetime currentBarTime = iTime(_Symbol, PERIOD_M1, 0);
   if(currentBarTime == 0) return;

   // New bar started — send the completed previous bar
   if(currentBarTime > _lastBarTime && _lastBarTime != 0)
   {
      MqlRates rates[1];
      if(CopyRates(_Symbol, PERIOD_M1, 1, 1, rates) == 1)
      {
         _network.SendBar(_Symbol, rates[0].time, rates[0].open, rates[0].high,
                          rates[0].low, rates[0].close, rates[0].tick_volume, false);
         _barsSent++;
      }
   }

   // Send partial (forming) bar at 1/sec rate limit
   if(_lastBarTime != 0 && currentBarTime == _lastBarTime)
   {
      if(_partialBarRateLimiter != NULL && _partialBarRateLimiter.TryAllow())
      {
         MqlRates rates[1];
         if(CopyRates(_Symbol, PERIOD_M1, 0, 1, rates) == 1)
         {
            _network.SendBar(_Symbol, rates[0].time, rates[0].open, rates[0].high,
                             rates[0].low, rates[0].close, rates[0].tick_volume, true);
            _partialBarsSent++;
         }
      }
   }

   _lastBarTime = currentBarTime;
}

// ═══════════════════════════════════════════════════════════════════
// Command Loop
// ═══════════════════════════════════════════════════════════════════

void PollCommands()
{
   if(_network == NULL || _dispatcher == NULL) return;

   // Process all available commands (non-blocking)
   while(true)
   {
      MessageEnvelope *env = _network.ReceiveCommand(0);
      if(env == NULL) break;

      // Duplicate detection
      long seqNum = env.SeqNum();
      if(seqNum > 0 && IsDuplicateCommand(seqNum))
      {
         _logger.Warning("Duplicate command ignored: " + env.MsgType() + " seq=" + IntegerToString(seqNum));
         _network.SendCommandAck(env.MsgType(), seqNum, true, "", "duplicate");
         delete env;
         continue;
      }

      _commandsReceived++;

      // Extract trade_id for ack
      string tradeId = env.PayloadString("trade_id");

      // Dispatch command
      bool success = _dispatcher.Dispatch(env);

      // Send command ack
      _network.SendCommandAck(env.MsgType(), seqNum, success, tradeId, success ? "" : "dispatch_failed");

      delete env;
   }
}

bool IsDuplicateCommand(long seqNum)
{
   int size = ArraySize(_processedSeqNums);
   for(int i = 0; i < size; i++)
      if(_processedSeqNums[i] == seqNum)
         return true;

   // Add to tracking
   ArrayResize(_processedSeqNums, size + 1);
   _processedSeqNums[size] = seqNum;

   // Prevent unbounded growth
   if(size >= MAX_TRACKED_SEQ_NUMS)
      ArrayResize(_processedSeqNums, 0);

   return false;
}

// ═══════════════════════════════════════════════════════════════════
// Crash Recovery & Position Sync
// ═══════════════════════════════════════════════════════════════════

void RestoreFromBroker()
{
   int total = PositionsTotal();
   int restored = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)_config.magicNumber) continue;

      string tradeId = PositionGetString(POSITION_COMMENT);
      if(StringLen(tradeId) == 0) tradeId = "mt5_" + IntegerToString(ticket);

      double slPoints = 0;
      double rrRatio = 1.0;
      _orderTracker.TrackEntry(tradeId, ticket, slPoints, rrRatio);
      restored++;
   }

   if(restored > 0)
      _logger.Info("[Sync] Restored " + IntegerToString(restored) + " position(s) from broker");
}

void ReportPositionsToPython()
{
   int total = PositionsTotal();
   JSONValue *positions = new JSONValue(JSON_ARRAY);
   JSONValue *untracked = new JSONValue(JSON_ARRAY);
   int count = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)_config.magicNumber) continue;

      string tradeId = PositionGetString(POSITION_COMMENT);
      if(StringLen(tradeId) == 0) tradeId = "mt5_" + IntegerToString(ticket);

      JSONValue *pos = new JSONValue(JSON_OBJECT);
      pos["trade_id"]   = new JSONValue(tradeId);
      pos["direction"]  = new JSONValue((PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "long" : "short");
      pos["entry_price"]= new JSONValue(PositionGetDouble(POSITION_PRICE_OPEN));
      pos["stop_loss"]  = new JSONValue(PositionGetDouble(POSITION_SL));
      pos["take_profit"]= new JSONValue(PositionGetDouble(POSITION_TP));
      pos["quantity"]   = new JSONValue(PositionGetDouble(POSITION_VOLUME));

      positions.Add(pos);
      // NOTE: Add() takes ownership — do NOT delete pos
      count++;
   }

   _network.SendPositionSync(positions, untracked);
   // NOTE: SendPositionSync puts arrays into a payload tree which is then deleted.
   // Do NOT delete positions or untracked here.

   if(count > 0)
      _logger.Info("[Sync] Reported " + IntegerToString(count) + " position(s) to Python");
}

// ═══════════════════════════════════════════════════════════════════
// Fill Detection (OnTrade)
// ═══════════════════════════════════════════════════════════════════

void ProcessNewDeals()
{
   // Load recent history (last hour)
   datetime from = TimeCurrent() - 3600;
   if(from < 0) from = 0;
   HistorySelect(from, TimeCurrent());

   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(IsDealProcessed(ticket)) continue;

      // Check magic number
      ulong magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != _config.magicNumber) continue;

      // Process this deal
      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(ticket, DEAL_REASON);
      double price = HistoryDealGetDouble(ticket, DEAL_PRICE);
      string comment = HistoryDealGetString(ticket, DEAL_COMMENT);
      ulong orderTicket = HistoryDealGetInteger(ticket, DEAL_ORDER);

      if(entry == DEAL_ENTRY_IN)
      {
         // Entry fill
         double sl = 0, tp = 0;
         // Try to get SL/TP from the associated order
         if(HistoryOrderSelect(orderTicket))
         {
            sl = HistoryOrderGetDouble(orderTicket, ORDER_SL);
            tp = HistoryOrderGetDouble(orderTicket, ORDER_TP);
         }
         _network.SendEntryFill(comment, price, sl, tp);
         _network.SendTradeLog(comment, "MT5:FILL", "Entry filled @ " + DoubleToString(price, 5));
         _logger.Success("ENTRY FILL: " + comment + " @ " + DoubleToString(price, 5));
      }
      else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
      {
         // Exit fill
         string resultType = "CLOSE";
         if(reason == DEAL_REASON_SL) resultType = "SL";
         else if(reason == DEAL_REASON_TP) resultType = "TP";

         _network.SendExitFill(comment, price, resultType);
         _network.SendTradeLog(comment, "MT5:FILL", resultType + " filled @ " + DoubleToString(price, 5));
         _logger.Info("EXIT FILL (" + resultType + "): " + comment + " @ " + DoubleToString(price, 5));

         // Clean up tracking
         if(_orderTracker != NULL)
            _orderTracker.RemoveTrade(comment);
      }

      MarkDealProcessed(ticket);
   }
}

bool IsDealProcessed(ulong ticket)
{
   int size = ArraySize(_processedDeals);
   for(int i = 0; i < size; i++)
      if(_processedDeals[i] == ticket)
         return true;
   return false;
}

void MarkDealProcessed(ulong ticket)
{
   int size = ArraySize(_processedDeals);
   if(size >= MAX_TRACKED_DEALS)
   {
      // Shift array left (FIFO)
      for(int i = 1; i < size; i++)
         _processedDeals[i - 1] = _processedDeals[i];
      _processedDeals[size - 1] = ticket;
   }
   else
   {
      ArrayResize(_processedDeals, size + 1);
      _processedDeals[size] = ticket;
   }
}

// ═══════════════════════════════════════════════════════════════════
// UI / Status Panel
// ═══════════════════════════════════════════════════════════════════

void UpdatePanel()
{
   if(_logger == NULL) return;

   string status = "Status: " + (_connected ? "CONNECTED" : "DISCONNECTED") + "\n" +
                   "Symbol: " + _Symbol + "\n" +
                   "Ticks: " + IntegerToString(_ticksSent) +
                   " | Bars: " + IntegerToString(_barsSent) +
                   " | Partial: " + IntegerToString(_partialBarsSent) + "\n" +
                   "Cmds: " + IntegerToString(_commandsReceived) +
                   " | Trades: " + IntegerToString(_orderTracker != NULL ? _orderTracker.GetActiveCount() : 0);

   _logger.UpdatePanel(status);
}

// ═══════════════════════════════════════════════════════════════════
// Cleanup
// ═══════════════════════════════════════════════════════════════════

void Cleanup()
{
   // Handlers (must be deleted before dispatcher)
   if(_handlerOpen != NULL)     { delete _handlerOpen; _handlerOpen = NULL; }
   if(_handlerClose != NULL)    { delete _handlerClose; _handlerClose = NULL; }
   if(_handlerModify != NULL)   { delete _handlerModify; _handlerModify = NULL; }
   if(_handlerRefresh != NULL)  { delete _handlerRefresh; _handlerRefresh = NULL; }
   if(_handlerTest != NULL)     { delete _handlerTest; _handlerTest = NULL; }

   if(_dispatcher != NULL)      { delete _dispatcher; _dispatcher = NULL; }
   if(_historyProvider != NULL) { delete _historyProvider; _historyProvider = NULL; }
   if(_network != NULL)         { _network.Dispose(); delete _network; _network = NULL; }
   if(_orderTracker != NULL)    { delete _orderTracker; _orderTracker = NULL; }
   if(_serializer != NULL)      { delete _serializer; _serializer = NULL; }
   if(_logger != NULL)          { delete _logger; _logger = NULL; }
   if(_config != NULL)          { delete _config; _config = NULL; }
   if(_tickRateLimiter != NULL) { delete _tickRateLimiter; _tickRateLimiter = NULL; }
   if(_partialBarRateLimiter != NULL) { delete _partialBarRateLimiter; _partialBarRateLimiter = NULL; }
}
