//+------------------------------------------------------------------+
//|                                      TradingBotZmqEA.mq5         |
//|  ZeroMQ connector for MetaTrader 5 → Python TradingBot           |
//|  SOLID architecture, clean dependency injection, feature parity  |
//|  with NinjaTrader connector.                                     |
//|                                                                  |
//|  v3.0: protocol alignment with the current Python↔platform ZMQ  |
//|  protocol — subscribe/unsubscribe/disconnect commands, account   |
//|  validation (terminal login), per-instrument order routing,     |
//|  refresh_start + order_rejected messages, connection watchdog    |
//|  with socket recovery, minimal connect handshake (no account).   |
//+------------------------------------------------------------------+
#property copyright "TradingBot"
#property link      ""
#property version   "3.0"
#property strict

#include <Zmq/Zmq.mqh>
#include <JSON/JSON.mqh>

#include "Domain/Contracts.mqh"
#include "Domain/MessageTypes.mqh"
#include "Domain/ValueObjects.mqh"
#include "Domain/PlatformApi.mqh"
#include "Domain/TickRateLimiter.mqh"
#include "Infrastructure/ConfigLoader.mqh"
#include "Infrastructure/Logger.mqh"
#include "Infrastructure/Serializers.mqh"
#include "Infrastructure/OrderTracking.mqh"
#include "Infrastructure/MqlPlatformApi.mqh"
#include "Application/ZmqNetwork.mqh"
#include "Application/CommandDispatcher.mqh"
#include "Application/E2ETestRunner.mqh"
#include "Application/HistoryProvider.mqh"
#include "Application/SubscriptionManager.mqh"
#include "Application/BrokerSync.mqh"
#include "Application/ConnectionWatchdog.mqh"
#include "Application/MarketStreamer.mqh"
#include "Commands/OrderOpenHandler.mqh"
#include "Commands/OrderCloseHandler.mqh"
#include "Commands/OrderModifyHandler.mqh"
#include "Commands/RefreshRequestHandler.mqh"
#include "Commands/SubscribeHandler.mqh"
#include "Commands/UnsubscribeHandler.mqh"
#include "Commands/DisconnectHandler.mqh"
#include "Commands/TestStartHandler.mqh"
#include "UI/ConnectorDialog.mqh"

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
MetaTraderLogger   *_logger;
IMessageSerializer *_serializer;
IOrderTracker      *_orderTracker;
IZmqNetwork        *_network;
ICommandDispatcher *_dispatcher;
IHistoryProvider   *_historyProvider;
SubscriptionManager *_subscriptions;
PlatformApis       *_apis;
BrokerSync         *_brokerSync;
ConnectionWatchdog *_watchdog;
MarketStreamer     *_streamer;

//--- Command handlers (tracked for cleanup)
OrderOpenHandler     *_handlerOpen;
OrderCloseHandler    *_handlerClose;
OrderModifyHandler   *_handlerModify;
RefreshRequestHandler *_handlerRefresh;
SubscribeHandler     *_handlerSubscribe;
UnsubscribeHandler   *_handlerUnsubscribe;
DisconnectHandler    *_handlerDisconnect;
TestStartHandler     *_handlerTest;

//--- State
bool               _connected = false;
bool               g_simulateTrades = false;   // Simulate mode: skip broker, send fake fills
bool               g_e2eTestRunning = false;   // Forces simulate mode during E2E tests
long               _seqNum = 0;
int                _heartbeatCounter = 0;

//--- Deferred disconnect (Python 'disconnect' command)
long               g_disconnectRequestTick = 0;

//--- Stats
long               _commandsReceived = 0;

//--- Duplicate detection
long               _processedSeqNums[];
const int          MAX_TRACKED_SEQ_NUMS = 1000;

//--- Pending refresh flag
bool               _pendingHistoryRefresh = false;

//--- Dialog UI
CConnectorDialog  *g_dialog = NULL;
bool               g_dialogCreated = false;

//+------------------------------------------------------------------+
//| UI Callbacks                                                     |
//+------------------------------------------------------------------+
void OnConnectClick(void)
{
   ToggleConnection();
}

void OnTestClick(void)
{
   TestConnection();
}

void OnE2EClick(void)
{
   RunE2ETests();
}

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

   // 2. Create logger
   _logger = new MetaTraderLogger("[ZMQ]");

   // 3. Create platform API seams (real MQL implementations)
   _apis = MqlPlatformApis::Create();

   // 4. Clean up any orphaned dialog from previous runs (Destroy can fail during OnDeinit)
   ForceRemoveOrphanedDialog();

   // 5. Create and show dialog
   g_dialog = new CConnectorDialog();
   if(!g_dialog.Create(0, "TradingBotZmqDialog", 0, 100, 100, 660, 460))
   {
      _logger.Error("Failed to create connector dialog");
      delete g_dialog;
      g_dialog = NULL;
      Cleanup();
      return INIT_FAILED;
   }
   g_dialogCreated = true;
   g_dialog.SetHandlers(OnConnectClick, OnTestClick, OnE2EClick);

   _logger.Info("Liquid ZMQ Connector UI ready");
   UpdatePanel();

   if(_config.autoConnectOnStartup)
      Connect();

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Cleanup(reason);
   Comment(""); // Clear chart comment
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!_connected) return;

   // 1. Stream ticks/bars/partials for all subscribed symbols
   _streamer.StreamAll();

   // 2. Poll for commands (non-blocking)
   PollCommands();

   // 3. Handle deferred disconnect (Python 'disconnect' command)
   HandleDeferredDisconnect();

   // 4. Handle pending history refresh
   if(_pendingHistoryRefresh)
   {
      _pendingHistoryRefresh = false;
      if(_historyProvider != NULL)
         _historyProvider.SendHistory();
   }

   // 5. Update panel periodically
   static datetime lastPanelUpdate = 0;
   if(_apis.time.Now() - lastPanelUpdate >= 1)
   {
      lastPanelUpdate = _apis.time.Now();
      UpdatePanel();
   }
}

//+------------------------------------------------------------------+
//| Timer function (heartbeat + command poll when market closed)     |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!_connected) return;

   // Poll for commands even when market is closed (OnTick not firing)
   PollCommands();

   // Handle deferred disconnect (Python 'disconnect' command)
   HandleDeferredDisconnect();

   // Stream subscribed symbols (OnTick only fires for the chart symbol)
   _streamer.StreamAll();

   _heartbeatCounter++;
   if(_heartbeatCounter >= _config.heartbeatSec)
   {
      _heartbeatCounter = 0;
      _network.SendHeartbeat("metatrader5", "ok");
   }

   _watchdog.OnTimerTick();
}

//+------------------------------------------------------------------+
//| Trade event function (detect fills)                              |
//+------------------------------------------------------------------+
void OnTrade()
{
   if(!_connected) return;
   _brokerSync.ProcessNewDeals();
}

//+------------------------------------------------------------------+
//| Chart event function (dialog UI)                                 |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   // Detect close-button click on the dialog caption (raw object click bypassing Controls event system)
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(StringFind(sparam, "TradingBotZmqDialog") >= 0 && StringFind(sparam, "Close") >= 0)
      {
         if(g_dialog != NULL)
         {
            g_dialog.Destroy();              // may fail silently
            ForceRemoveOrphanedDialog();     // brute-force remove any leftovers
            delete g_dialog;
            g_dialog = NULL;
            g_dialogCreated = false;
         }
         return;
      }
   }

   if(g_dialog == NULL) return;

   // Forward all chart events to the dialog for processing
   g_dialog.ChartEvent(id, lparam, dparam, sparam);
}

// ═══════════════════════════════════════════════════════════════════
// Connection Management
// ═══════════════════════════════════════════════════════════════════

void Connect()
{
   if(_connected) return;

   if(_logger != NULL)
      _logger.Info("Starting ZeroMQ connection...");

   // Create dependencies (Dependency Injection)
   _serializer = new JsonMessageSerializer(_logger);
   _orderTracker = new OrderStateManager();
   _network = new ZmqNetwork(_config, _serializer, _logger);
   _historyProvider = new HistoryProvider(_network, _logger, _config, _config.pair, _apis.marketData, _apis.time);
   _subscriptions = new SubscriptionManager(_logger, _config.maxTicksPerSecond, _apis.symbol, _apis.time);
   _brokerSync = new BrokerSync(_apis.position, _apis.dealHistory, _apis.account, _apis.time,
                                _orderTracker, _network, _logger, _config.magicNumber);
   _streamer = new MarketStreamer(_subscriptions, _network, _apis.symbol, _apis.marketData);
   _watchdog = new ConnectionWatchdog(_network, _brokerSync, _subscriptions, _logger, _config, _apis.symbol);

   // Connect ZMQ
   if(!_network.Start())
   {
      if(_logger != NULL)
         _logger.Error("Failed to start ZMQ network");
      Disconnect();
      return;
   }

   // Send connect handshake (minimal — no account field, mirroring NinjaTrader)
   _network.SendConnect("metatrader5", _config.platformVersion, _config.pair);
   if(_logger != NULL)
      _logger.Success("Connected to Python TradingBot via ZeroMQ");

   // Auto-subscribe the configured pair as a bootstrap so streaming starts
   // immediately. The source of truth after connect is subscribe/unsubscribe
   // commands — Python re-drives subscribe for its instrument anyway (the
   // SubscriptionManager is idempotent).
   if(StringLen(_config.pair) > 0)
      _subscriptions.Add(_config.pair);

   // Restore order tracking from broker (crash recovery)
   _brokerSync.RestoreFromBroker();

   // Report positions to Python (source of truth sync)
   _brokerSync.ReportPositionsToPython();

   // Setup command dispatcher
   // NOTE: Historical data is NOT sent automatically on connect.
   // Python requests it explicitly via REFRESH_REQUEST when needed.
   _dispatcher = new CommandDispatcher(_logger);

   // Capture simulate mode from UI checkbox or config at connect time
   bool simulate = g_simulateTrades;
   if(g_dialog != NULL && g_dialog.IsSimulateChecked())
      simulate = true;
   if(_config.simulateTrades)
      simulate = true;
   g_simulateTrades = simulate;

   _handlerOpen = new OrderOpenHandler(_network, _logger, _orderTracker, _config, _apis, simulate);
   _handlerClose = new OrderCloseHandler(_network, _logger, _orderTracker, _config, _apis, simulate);
   _handlerModify = new OrderModifyHandler(_network, _logger, _orderTracker, _config, _apis, simulate);
   _handlerRefresh = new RefreshRequestHandler(_network, _logger, _historyProvider, _apis);
   _handlerSubscribe = new SubscribeHandler(_network, _logger, _subscriptions);
   _handlerUnsubscribe = new UnsubscribeHandler(_network, _logger, _subscriptions);
   _handlerDisconnect = new DisconnectHandler(_network, _logger);
   _handlerTest = new TestStartHandler(_network, _logger);

   _dispatcher.Register(_handlerOpen);
   _dispatcher.Register(_handlerClose);
   _dispatcher.Register(_handlerModify);
   _dispatcher.Register(_handlerRefresh);
   _dispatcher.Register(_handlerSubscribe);
   _dispatcher.Register(_handlerUnsubscribe);
   _dispatcher.Register(_handlerDisconnect);
   _dispatcher.Register(_handlerTest);

   // Start heartbeat timer (1-second granularity)
   EventSetTimer(1);

   _connected = true;
   if(_logger != NULL)
      _logger.Info("Command dispatcher ready. Handlers: open, close, modify, refresh, subscribe, unsubscribe, disconnect, test");
   UpdatePanel();
}

void Disconnect()
{
   if(!_connected && _network == NULL) return;

   if(_logger != NULL)
      _logger.Info("Disconnecting...");

   _connected = false;
   EventKillTimer();
   Sleep(100); // Let in-flight sends drain

   // Handlers (must be deleted before dispatcher)
   if(_handlerOpen != NULL)        { delete _handlerOpen; _handlerOpen = NULL; }
   if(_handlerClose != NULL)       { delete _handlerClose; _handlerClose = NULL; }
   if(_handlerModify != NULL)      { delete _handlerModify; _handlerModify = NULL; }
   if(_handlerRefresh != NULL)     { delete _handlerRefresh; _handlerRefresh = NULL; }
   if(_handlerSubscribe != NULL)   { delete _handlerSubscribe; _handlerSubscribe = NULL; }
   if(_handlerUnsubscribe != NULL) { delete _handlerUnsubscribe; _handlerUnsubscribe = NULL; }
   if(_handlerDisconnect != NULL)  { delete _handlerDisconnect; _handlerDisconnect = NULL; }
   if(_handlerTest != NULL)        { delete _handlerTest; _handlerTest = NULL; }

   if(_dispatcher != NULL)      { delete _dispatcher; _dispatcher = NULL; }
   if(_watchdog != NULL)        { delete _watchdog; _watchdog = NULL; }
   if(_streamer != NULL)        { delete _streamer; _streamer = NULL; }
   if(_brokerSync != NULL)      { delete _brokerSync; _brokerSync = NULL; }
   if(_subscriptions != NULL)   { delete _subscriptions; _subscriptions = NULL; }
   if(_historyProvider != NULL) { delete _historyProvider; _historyProvider = NULL; }
   if(_network != NULL)         { _network.Dispose(); delete _network; _network = NULL; }
   if(_orderTracker != NULL)    { delete _orderTracker; _orderTracker = NULL; }
   if(_serializer != NULL)      { delete _serializer; _serializer = NULL; }

   // Reset stats
   _commandsReceived = 0;
   _heartbeatCounter = 0;
   _seqNum = 0;
   g_disconnectRequestTick = 0;
   ArrayResize(_processedSeqNums, 0);
   _pendingHistoryRefresh = false;

   if(_logger != NULL)
      _logger.Info("Disconnected from Python TradingBot");
   UpdatePanel();
}

void ToggleConnection()
{
   if(_connected) Disconnect();
   else Connect();
}

void TestConnection()
{
   if(!_connected || _network == NULL)
   {
      if(_logger != NULL)
         _logger.Warning("Not connected. Click Connect first.");
      return;
   }

   if(_logger != NULL)
      _logger.Info("=== TEST CONNECTION ===");

   bool success = _network.SendTestPingWithResponse(2000);
   if(success)
   {
      if(_logger != NULL)
         _logger.Success("TEST CONNECTION: PASSED - ZMQ REQ/REP working");
   }
   else
   {
      if(_logger != NULL)
         _logger.Warning("TEST CONNECTION: FAILED - No response from Python");
   }
}

void RunE2ETests()
{
   if(!_connected || _network == NULL)
   {
      if(_logger != NULL)
         _logger.Warning("Not connected. Click Connect first.");
      return;
   }

   if(g_dialog != NULL)
      g_dialog.SetButtonEnabled(2, false);

   // Force simulate mode during E2E tests (safety)
   g_e2eTestRunning = true;

   E2ETestRunner runner(_network, _logger, _apis.account);
   runner.RunAllScenarios();

   // Reset test flag
   g_e2eTestRunning = false;

   if(g_dialog != NULL)
      g_dialog.SetButtonEnabled(2, true);
}

// ═══════════════════════════════════════════════════════════════════
// Deferred Disconnect (Python 'disconnect' command)
// ═══════════════════════════════════════════════════════════════════

// Python's disconnect command is ACKed first; teardown happens here ~100ms
// later so the ACK goes out and a deliberate stop never triggers recovery.
void HandleDeferredDisconnect()
{
   if(g_disconnectRequestTick == 0) return;
   if(_apis.time.TickCount() - g_disconnectRequestTick < 100) return;
   g_disconnectRequestTick = 0;

   if(_logger != NULL)
      _logger.Info("Python requested disconnect — tearing down quietly (no recovery)");
   Disconnect();
}

// ═══════════════════════════════════════════════════════════════════
// Command Loop
// ═══════════════════════════════════════════════════════════════════

void PollCommands()
{
   if(_network == NULL || _dispatcher == NULL) return;

   // Process available commands (non-blocking, capped)
   const int MAX_COMMANDS_PER_TICK = 10;
   for(int cmdCount = 0; cmdCount < MAX_COMMANDS_PER_TICK; cmdCount++)
   {
      MessageEnvelope *env = _network.ReceiveCommand(0);
      if(env == NULL) break;

      string msgType = env.MsgType();
      long seqNum = env.SeqNum();
      string tradeId = env.PayloadString("trade_id");

      // Duplicate detection
      if(seqNum > 0 && IsDuplicateCommand(seqNum))
      {
         if(_logger != NULL)
            _logger.Warning("Duplicate command ignored: " + msgType + " seq=" + IntegerToString(seqNum));
         _network.SendCommandAck(msgType, seqNum, true, tradeId, "duplicate");
         delete env;
         continue;
      }

      _commandsReceived++;

      // Dispatch command with detailed error handling
      bool success = _dispatcher.Dispatch(env);

      if(success)
      {
         _network.SendCommandAck(msgType, seqNum, true, tradeId, "");
      }
      else
      {
         if(_logger != NULL)
            _logger.Error("Command dispatch failed: " + msgType);
         _network.SendCommandAck(msgType, seqNum, false, tradeId, "handler returned failure");
         _network.SendError("metatrader5", "command_dispatch_failed", msgType + ": handler returned failure");
      }

      delete env;

      if(_commandsReceived % 10 == 0)
         UpdatePanel();
   }
}

bool IsDuplicateCommand(long seqNum)
{
   int size = ArraySize(_processedSeqNums);
   for(int i = 0; i < size; i++)
      if(_processedSeqNums[i] == seqNum)
         return true;

   // Prevent unbounded growth — shift out oldest half first
   if(size >= MAX_TRACKED_SEQ_NUMS)
   {
      int shift = MAX_TRACKED_SEQ_NUMS / 2;
      for(int i = 0; i < shift; i++)
         _processedSeqNums[i] = _processedSeqNums[i + shift];
      ArrayResize(_processedSeqNums, shift);
      size = shift;
   }

   // Add to tracking
   ArrayResize(_processedSeqNums, size + 1);
   _processedSeqNums[size] = seqNum;

   return false;
}

// ═══════════════════════════════════════════════════════════════════
// UI / Status Panel
// ═══════════════════════════════════════════════════════════════════

void UpdatePanel()
{
   string stats = "Ticks: " + IntegerToString(_streamer != NULL ? _streamer.TicksSent() : 0) +
                  " | Bars: " + IntegerToString(_streamer != NULL ? _streamer.BarsSent() : 0) +
                  " | Partial: " + IntegerToString(_streamer != NULL ? _streamer.PartialBarsSent() : 0) + "\n" +
                  "Cmds: " + IntegerToString(_commandsReceived) +
                  " | Trades: " + IntegerToString(_orderTracker != NULL ? _orderTracker.GetActiveCount() : 0);

   if(g_dialog != NULL)
   {
      g_dialog.UpdateStatus(_connected, stats);
      g_dialog.SetButtonEnabled(1, _connected); // Test Connection
      g_dialog.SetButtonEnabled(2, _connected); // Run E2E Tests
   }
   else if(_logger != NULL)
   {
      string status = "Status: " + (_connected ? "CONNECTED" : "DISCONNECTED") + "\n" +
                      "Symbol: " + _Symbol + "\n" + stats;
      MetaTraderLogger *panelLogger = (MetaTraderLogger *)_logger;
      panelLogger.UpdatePanel(status);
   }
}

// ═══════════════════════════════════════════════════════════════════
// Brute-force dialog removal (Controls library objects resist batch delete)
// ═══════════════════════════════════════════════════════════════════

void ForceRemoveOrphanedDialog()
{
   long chartId = ChartID();
   // First try batch delete
   ObjectsDeleteAll(chartId, "TradingBotZmqDialog", -1, -1);

   // Then individually hunt down any survivors
   for(int sub = 0; sub <= 1; sub++)
   {
      int total = ObjectsTotal(chartId, sub, -1);
      for(int i = total - 1; i >= 0; i--)
      {
         string objName = ObjectName(chartId, i, sub, -1);
         if(StringFind(objName, "TradingBotZmqDialog") >= 0)
         {
            ObjectDelete(chartId, objName);
         }
      }
   }
   ChartRedraw();
}

// ═══════════════════════════════════════════════════════════════════
// Cleanup
// ═══════════════════════════════════════════════════════════════════

void Cleanup(const int reason = 0)
{
   Disconnect();

   if(g_dialog != NULL)
   {
      if(g_dialogCreated)
      {
         g_dialog.Destroy(reason);              // may fail silently
         ForceRemoveOrphanedDialog();            // brute-force remove any leftovers
      }
      delete g_dialog;
      g_dialog = NULL;
      g_dialogCreated = false;
   }

   if(_logger != NULL) { delete _logger; _logger = NULL; }
   if(_apis != NULL)   { MqlPlatformApis::Destroy(_apis); _apis = NULL; }
   if(_config != NULL) { delete _config; _config = NULL; }
}
