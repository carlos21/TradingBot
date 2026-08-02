//+------------------------------------------------------------------+
//|                              Application/ConnectionWatchdog.mqh  |
//|  Query-channel ping watchdog + socket recovery state machine.    |
//|  Driven by the EA's 1-second OnTimer via OnTimerTick().          |
//|  Extracted from TradingBotZmqEA.mq5 (no behavior change).        |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Domain/PlatformApi.mqh"
#include "BrokerSync.mqh"
#include "SubscriptionManager.mqh"

//--- Watchdog tuning (ping query channel; recreate sockets on silence)
const int WATCHDOG_PING_SEC          = 5;
const int WATCHDOG_BACKOFF_SEC       = 300;
const int WATCHDOG_FAILURE_THRESHOLD = 3;
const int WATCHDOG_BACKOFF_THRESHOLD = 3;
const int WATCHDOG_PING_TIMEOUT_MS   = 2000;

//+------------------------------------------------------------------+
//| ConnectionWatchdog — detects a dead Python command channel: the  |
//|  passive PULL socket never notices a half-open TCP connection    |
//|  on its own. Every WATCHDOG_PING_SEC seconds ping the REQ/REP    |
//|  query channel; after WATCHDOG_FAILURE_THRESHOLD consecutive     |
//|  failures recreate ALL sockets and resync. After                 |
//|  WATCHDOG_BACKOFF_THRESHOLD failed recovery cycles, back off to  |
//|  a WATCHDOG_BACKOFF_SEC ping interval. Mirrors the NT connector. |
//+------------------------------------------------------------------+
class ConnectionWatchdog
{
private:
   IZmqNetwork         *m_network;
   BrokerSync          *m_brokerSync;
   SubscriptionManager *m_subscriptions;
   ILogger             *m_logger;
   ZmqConfiguration    *m_config;
   ISymbolApi          *m_symbolApi;

   int                  m_counter;
   int                  m_failures;
   int                  m_cycles;
   int                  m_intervalSec;

public:
   ConnectionWatchdog(IZmqNetwork *network, BrokerSync *brokerSync, SubscriptionManager *subscriptions,
                      ILogger *logger, ZmqConfiguration *config, ISymbolApi *symbolApi)
   {
      m_network = network;
      m_brokerSync = brokerSync;
      m_subscriptions = subscriptions;
      m_logger = logger;
      m_config = config;
      m_symbolApi = symbolApi;
      m_counter = 0;
      m_failures = 0;
      m_cycles = 0;
      m_intervalSec = WATCHDOG_PING_SEC;
   }

   ~ConnectionWatchdog() {}

   //--- Called from the EA's 1-second OnTimer
   void OnTimerTick()
   {
      if(m_network == NULL) return;

      m_counter++;
      if(m_counter < m_intervalSec) return;
      m_counter = 0;

      bool reachable = m_network.SendTestPingWithResponse(WATCHDOG_PING_TIMEOUT_MS);
      if(reachable)
      {
         m_failures = 0;
         m_cycles = 0;
         if(m_intervalSec != WATCHDOG_PING_SEC)
         {
            m_intervalSec = WATCHDOG_PING_SEC;
            if(m_logger != NULL)
               m_logger.Info("Python reachable again — connection watchdog back to normal ping interval");
         }
         return;
      }

      m_failures++;
      if(m_failures < WATCHDOG_FAILURE_THRESHOLD) return;
      m_failures = 0;

      if(m_logger != NULL)
         m_logger.Warning("Python unreachable on query channel after " + IntegerToString(WATCHDOG_FAILURE_THRESHOLD) + " failed pings — recreating ZMQ sockets");
      RecoverConnection();

      m_cycles++;
      if(m_cycles >= WATCHDOG_BACKOFF_THRESHOLD && m_intervalSec != WATCHDOG_BACKOFF_SEC)
      {
         m_intervalSec = WATCHDOG_BACKOFF_SEC;
         if(m_logger != NULL)
            m_logger.Warning("Python still unreachable — backing off to " + IntegerToString(WATCHDOG_BACKOFF_SEC) + "s ping interval");
      }
   }

private:
   void RecoverConnection()
   {
      if(m_logger != NULL)
         m_logger.Warning("!!! CONNECTION RECOVERY — recreating ZMQ sockets");

      if(!m_network.Restart())
      {
         if(m_logger != NULL)
            m_logger.Error("Connection recovery failed to restart ZMQ network");
         return;
      }

      // Re-announce so Python re-drives its subscribe+refresh flow
      m_network.SendConnect("metatrader5", m_config.platformVersion, m_config.pair);

      // Broker is the source of truth — resync positions after reconnect
      m_brokerSync.RestoreFromBroker();
      m_brokerSync.ReportPositionsToPython();

      // Re-subscribe previously subscribed symbols (the subscription list
      // survives the socket restart; re-select at the broker to be safe)
      if(m_subscriptions != NULL)
      {
         int count = m_subscriptions.Count();
         for(int i = 0; i < count; i++)
            m_symbolApi.Select(m_subscriptions.Symbol(i));
         if(m_logger != NULL)
            m_logger.Info("[Recovery] Re-subscribed " + IntegerToString(count) + " symbol(s)");
      }

      if(m_logger != NULL)
         m_logger.Success("Connection recovery complete");
   }
};
