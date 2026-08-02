//+------------------------------------------------------------------+
//|                    Tests/Suites/TestConnectionWatchdog.mqh       |
//|  Suite: ConnectionWatchdog — ping cadence, failure threshold,    |
//|  socket recovery (restart + connect + resync + re-subscribe),    |
//|  success reset, and backoff to the slow ping interval.           |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Infrastructure/OrderTracking.mqh"
#include "../../Application/ConnectionWatchdog.mqh"

//+------------------------------------------------------------------+
//| _WdTicks — drive the watchdog n 1-second timer ticks             |
//+------------------------------------------------------------------+
void _WdTicks(ConnectionWatchdog *watchdog, int n)
{
   for(int i = 0; i < n; i++)
      watchdog.OnTimerTick();
}

//+------------------------------------------------------------------+
//| RunConnectionWatchdogTests                                       |
//+------------------------------------------------------------------+
void RunConnectionWatchdogTests()
{
   //--- Ping cadence: first ping at tick 5, then every 5 ticks
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 4);
      AssertEqualLong(0, network.SentCount(MT_TEST_PING), "Watchdog: no ping before tick 5");
      _WdTicks(GetPointer(watchdog), 1);
      AssertEqualLong(1, network.SentCount(MT_TEST_PING), "Watchdog: first ping at tick 5");
      _WdTicks(GetPointer(watchdog), 4);
      AssertEqualLong(1, network.SentCount(MT_TEST_PING), "Watchdog: counter resets after ping");
      _WdTicks(GetPointer(watchdog), 1);
      AssertEqualLong(2, network.SentCount(MT_TEST_PING), "Watchdog: second ping at tick 10");
      AssertEqualLong(0, network.RestartCount(), "Watchdog: healthy pings never restart");
   }

   //--- 1-2 consecutive failures stay below the recovery threshold
   {
      FakeZmqNetwork network;
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 10);
      AssertEqualLong(2, network.SentCount(MT_TEST_PING), "Watchdog: two failed pings sent");
      AssertEqualLong(0, network.RestartCount(), "Watchdog: two failures do not restart");
      AssertEqualLong(0, network.SentCount(MT_CONNECT), "Watchdog: two failures send no connect");
   }

   //--- 3rd consecutive failure → full recovery: restart, connect,
   //--- broker resync, subscription re-select
   {
      FakeZmqNetwork network;
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      position.AddPosition(7001, 424242, "T-w1", (long)POSITION_TYPE_BUY, 1.10000, 1.09500, 1.11000, 1.0);
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      subscriptions.Add("GBPUSD");
      AssertEqualLong(1, symbols.SelectCount("EURUSD"), "Watchdog: subscribe selects symbol once");
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 15);
      AssertEqualLong(3, network.SentCount(MT_TEST_PING), "Watchdog: three failed pings sent");
      AssertEqualLong(1, network.RestartCount(), "Watchdog: third failure restarts network once");
      AssertEqualLong(1, network.SentCount(MT_CONNECT), "Watchdog: recovery resends connect");
      int connIdx = network.FindByMsgType(MT_CONNECT);
      AssertTrue(network.PayloadAtContains(connIdx, "platform=metatrader5"), "Watchdog: connect carries platform");
      AssertTrue(network.PayloadAtContains(connIdx, "version=3.0.0"), "Watchdog: connect carries version");
      AssertTrue(network.PayloadAtContains(connIdx, "pair=EURUSD"), "Watchdog: connect carries pair");
      AssertEqualLong(1, network.SentCount(MT_POSITION_SYNC), "Watchdog: recovery resyncs positions");
      ulong ticket = 0;
      double slPoints = 0, rrRatio = 0;
      AssertTrue(tracker.TryGetEntry("T-w1", ticket, slPoints, rrRatio), "Watchdog: recovery restores broker positions");
      AssertEqualLong(2, symbols.SelectCount("EURUSD"), "Watchdog: recovery re-selects EURUSD");
      AssertEqualLong(2, symbols.SelectCount("GBPUSD"), "Watchdog: recovery re-selects GBPUSD");
      AssertTrue(logger.Contains("CONNECTION RECOVERY"), "Watchdog: recovery logged");
      AssertTrue(logger.Contains("Connection recovery complete"), "Watchdog: recovery completion logged");
   }

   //--- A successful ping resets the consecutive-failure counter
   {
      FakeZmqNetwork network;
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      network.QueuePingResult(true);
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      network.QueuePingResult(true);
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 30);
      AssertEqualLong(6, network.SentCount(MT_TEST_PING), "Watchdog: six pings in 30 ticks");
      AssertEqualLong(0, network.RestartCount(), "Watchdog: interleaved success prevents recovery");
   }

   //--- 3 failed recovery cycles → backoff to 300s; success restores 5s
   {
      FakeZmqNetwork network;
      for(int i = 0; i < 10; i++)
         network.QueuePingResult(false);
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 45);   //--- 9 failed pings = 3 recovery cycles
      AssertEqualLong(9, network.SentCount(MT_TEST_PING), "Watchdog: nine failed pings sent");
      AssertEqualLong(3, network.RestartCount(), "Watchdog: three recovery cycles ran");
      AssertEqualLong(3, network.SentCount(MT_CONNECT), "Watchdog: each cycle resends connect");
      AssertTrue(logger.Contains("backing off to 300s"), "Watchdog: backoff logged");

      _WdTicks(GetPointer(watchdog), 299);
      AssertEqualLong(9, network.SentCount(MT_TEST_PING), "Watchdog: backoff holds pings for 299 ticks");
      _WdTicks(GetPointer(watchdog), 1);
      AssertEqualLong(10, network.SentCount(MT_TEST_PING), "Watchdog: ping again at tick 300 of backoff");
      AssertEqualLong(3, network.RestartCount(), "Watchdog: single backoff failure does not restart");

      _WdTicks(GetPointer(watchdog), 300);  //--- ping 11 succeeds (queue exhausted → default true)
      AssertEqualLong(11, network.SentCount(MT_TEST_PING), "Watchdog: successful ping during backoff");
      AssertTrue(logger.Contains("back to normal ping interval"), "Watchdog: interval restore logged");
      _WdTicks(GetPointer(watchdog), 4);
      AssertEqualLong(11, network.SentCount(MT_TEST_PING), "Watchdog: restored interval waits 5 ticks");
      _WdTicks(GetPointer(watchdog), 1);
      AssertEqualLong(12, network.SentCount(MT_TEST_PING), "Watchdog: restored 5s cadence pings");
   }

   //--- Restart failure → recovery aborts before connect/resync
   {
      FakeZmqNetwork network;
      network.SetStartResult(false);
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      network.QueuePingResult(false);
      FakeLogger logger;
      FakeAccountApi account;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      FakePositionApi position;
      FakeDealHistoryApi dealHistory;
      OrderStateManager tracker;
      ZmqConfiguration config;
      BrokerSync sync(GetPointer(position), GetPointer(dealHistory), GetPointer(account),
                      GetPointer(time), GetPointer(tracker), GetPointer(network),
                      GetPointer(logger), config.magicNumber);
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      ConnectionWatchdog watchdog(GetPointer(network), GetPointer(sync), GetPointer(subscriptions),
                                  GetPointer(logger), GetPointer(config), GetPointer(symbols));

      _WdTicks(GetPointer(watchdog), 15);
      AssertEqualLong(1, network.RestartCount(), "Watchdog: failed restart still attempted once");
      AssertEqualLong(0, network.SentCount(MT_CONNECT), "Watchdog: failed restart sends no connect");
      AssertEqualLong(0, network.SentCount(MT_POSITION_SYNC), "Watchdog: failed restart skips resync");
      AssertTrue(logger.Contains("Connection recovery failed to restart ZMQ network"),
                 "Watchdog: restart failure logged");
   }
}
//+------------------------------------------------------------------+
