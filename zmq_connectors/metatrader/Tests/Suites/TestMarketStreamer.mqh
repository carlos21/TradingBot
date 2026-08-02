//+------------------------------------------------------------------+
//|                      Tests/Suites/TestMarketStreamer.mqh         |
//|  Suite: MarketStreamer — tick streaming with time_msc dedupe     |
//|  and rate limiting, completed-bar detection on bar advance,      |
//|  1/sec partial bars, multi-symbol independence, stats getters.   |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Application/MarketStreamer.mqh"

//+------------------------------------------------------------------+
//| _MsRate — build one series-order MqlRates entry                  |
//+------------------------------------------------------------------+
void _MsRate(MqlRates &rate, datetime time, double open, double high,
             double low, double close, long volume)
{
   rate.time = time;
   rate.open = open;
   rate.high = high;
   rate.low = low;
   rate.close = close;
   rate.tick_volume = volume;
   rate.spread = 0;
   rate.real_volume = 0;
}

//+------------------------------------------------------------------+
//| RunMarketStreamerTests                                           |
//+------------------------------------------------------------------+
void RunMarketStreamerTests()
{
   //--- Tick sent for a subscribed symbol; dedupe by time_msc across
   //--- repeated StreamAll calls (OnTick + OnTimer double-drive)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      MarketStreamer streamer(GetPointer(subscriptions), GetPointer(network),
                              GetPointer(symbols), GetPointer(marketData));

      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10005, 100, (datetime)1700000000, 1700000000123);
      streamer.StreamAll();
      AssertEqualLong(1, network.SentCount(MT_TICK), "Streamer: subscribed symbol sends tick");
      AssertTrue(network.PayloadAtContains(0, "pair=EURUSD"), "Streamer: tick carries pair");
      AssertTrue(network.PayloadAtContains(0, "price=1.100050"), "Streamer: tick priced at last");
      AssertTrue(network.PayloadAtContains(0, "volume=100"), "Streamer: tick carries volume");
      AssertTrue(network.PayloadAtContains(0, "time=1700000000"), "Streamer: tick carries time");
      AssertEqualLong(1, streamer.TicksSent(), "Streamer: TicksSent counts the tick");

      streamer.StreamAll();   //--- same time_msc: OnTick/OnTimer double-drive
      AssertEqualLong(1, network.SentCount(MT_TICK), "Streamer: duplicate time_msc deduped");
      AssertEqualLong(1, streamer.TicksSent(), "Streamer: dedupe does not count");

      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10006, 101, (datetime)1700000000, 1700000000456);
      streamer.StreamAll();
      AssertEqualLong(2, network.SentCount(MT_TICK), "Streamer: new time_msc sends again");
      AssertTrue(network.PayloadAtContains(1, "price=1.100060"), "Streamer: second tick carries new price");
      AssertEqualLong(2, streamer.TicksSent(), "Streamer: TicksSent accumulates");

      //--- last == 0 falls back to bid
      symbols.SetTick("EURUSD", 1.10000, 1.10010, 0.0, 0, (datetime)1700000001, 1700000001000);
      streamer.StreamAll();
      AssertEqualLong(3, network.SentCount(MT_TICK), "Streamer: zero-last tick still sent");
      AssertTrue(network.PayloadAtContains(2, "price=1.100000"), "Streamer: zero-last tick priced at bid");
   }

   //--- Tick rate limiter enforced per symbol per second
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      MarketStreamer streamer(GetPointer(subscriptions), GetPointer(network),
                              GetPointer(symbols), GetPointer(marketData));

      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10001, 1, (datetime)1700000000, 1);
      streamer.StreamAll();
      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10002, 1, (datetime)1700000000, 2);
      streamer.StreamAll();
      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10003, 1, (datetime)1700000000, 3);
      streamer.StreamAll();
      AssertEqualLong(2, network.SentCount(MT_TICK), "Streamer: third tick in window rate-limited");
      AssertEqualLong(2, streamer.TicksSent(), "Streamer: limited tick not counted");

      time.AdvanceSeconds(1);   //--- new 1-second window
      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10004, 1, (datetime)1700000001, 4);
      streamer.StreamAll();
      AssertEqualLong(3, network.SentCount(MT_TICK), "Streamer: window reset allows tick again");
      AssertEqualLong(3, streamer.TicksSent(), "Streamer: post-reset tick counted");
   }

   //--- Completed bar sent once when the bar time advances; nothing on
   //--- first observation (lastBarTime == 0 init)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      MarketStreamer streamer(GetPointer(subscriptions), GetPointer(network),
                              GetPointer(symbols), GetPointer(marketData));

      MqlRates rates[2];
      _MsRate(rates[0], (datetime)1700000060, 1.10010, 1.10060, 1.10000, 1.10030, 500);
      _MsRate(rates[1], (datetime)1700000000, 1.10000, 1.10050, 1.09950, 1.10020, 1000);
      marketData.SetRates("EURUSD", rates);
      marketData.SetCurrentBarTime("EURUSD", (datetime)1700000000);

      streamer.StreamAll();   //--- first observation: baseline only
      AssertEqualLong(0, network.SentCount(MT_BAR), "Streamer: first observation sends no bar");
      AssertEqualLong(0, network.SentCount(MT_PARTIAL), "Streamer: first observation sends no partial");
      AssertEqualLong(0, streamer.BarsSent(), "Streamer: BarsSent zero at init");
      AssertEqualLong(0, streamer.PartialBarsSent(), "Streamer: PartialBarsSent zero at init");

      marketData.SetCurrentBarTime("EURUSD", (datetime)1700000060);
      streamer.StreamAll();   //--- bar advanced → completed previous bar
      AssertEqualLong(1, network.SentCount(MT_BAR), "Streamer: bar advance sends completed bar");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "time=1700000000"),
                 "Streamer: completed bar is the previous bar");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "open=1.100000"), "Streamer: bar open");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "high=1.100500"), "Streamer: bar high");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "low=1.099500"), "Streamer: bar low");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "close=1.100200"), "Streamer: bar close");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_BAR), "volume=1000"), "Streamer: bar volume");
      AssertEqualLong(1, streamer.BarsSent(), "Streamer: BarsSent counts completed bar");

      streamer.StreamAll();   //--- same bar time: no re-send of completed bar
      AssertEqualLong(1, network.SentCount(MT_BAR), "Streamer: completed bar sent only once");
      AssertEqualLong(1, streamer.BarsSent(), "Streamer: BarsSent unchanged on same bar");
   }

   //--- Partial (forming) bar streamed at most once per second
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      MarketStreamer streamer(GetPointer(subscriptions), GetPointer(network),
                              GetPointer(symbols), GetPointer(marketData));

      MqlRates rates[1];
      _MsRate(rates[0], (datetime)1700000000, 1.10010, 1.10060, 1.10000, 1.10030, 500);
      marketData.SetRates("EURUSD", rates);
      marketData.SetCurrentBarTime("EURUSD", (datetime)1700000000);

      streamer.StreamAll();   //--- init baseline
      streamer.StreamAll();   //--- same bar → first partial allowed
      AssertEqualLong(1, network.SentCount(MT_PARTIAL), "Streamer: forming bar sends partial");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_PARTIAL), "time=1700000000"),
                 "Streamer: partial carries forming-bar time");
      AssertTrue(network.PayloadAtContains(network.FindByMsgType(MT_PARTIAL), "close=1.100300"),
                 "Streamer: partial carries forming-bar close");
      AssertEqualLong(1, streamer.PartialBarsSent(), "Streamer: PartialBarsSent counts partial");

      streamer.StreamAll();   //--- same second → partial limiter blocks
      AssertEqualLong(1, network.SentCount(MT_PARTIAL), "Streamer: partial limited to 1/sec");
      AssertEqualLong(1, streamer.PartialBarsSent(), "Streamer: blocked partial not counted");

      time.AdvanceSeconds(1);
      streamer.StreamAll();   //--- new second → partial again
      AssertEqualLong(2, network.SentCount(MT_PARTIAL), "Streamer: partial again after 1s");
      AssertEqualLong(2, streamer.PartialBarsSent(), "Streamer: second partial counted");
   }

   //--- Multi-symbol independence: per-symbol dedupe and counting
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");
      subscriptions.Add("GBPUSD");
      MarketStreamer streamer(GetPointer(subscriptions), GetPointer(network),
                              GetPointer(symbols), GetPointer(marketData));

      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10005, 10, (datetime)1700000000, 1000);
      symbols.SetTick("GBPUSD", 1.25000, 1.25010, 1.25005, 20, (datetime)1700000000, 2000);
      streamer.StreamAll();
      AssertEqualLong(2, network.SentCount(MT_TICK), "Streamer: both symbols tick");
      AssertTrue(network.PayloadAtContains(0, "pair=EURUSD"), "Streamer: first symbol tick first");
      AssertTrue(network.PayloadAtContains(1, "pair=GBPUSD"), "Streamer: second symbol tick second");
      AssertTrue(network.PayloadAtContains(1, "price=1.250050"), "Streamer: GBPUSD priced independently");

      //--- Only EURUSD moves → only EURUSD re-sends
      symbols.SetTick("EURUSD", 1.10000, 1.10010, 1.10007, 11, (datetime)1700000001, 3000);
      streamer.StreamAll();
      AssertEqualLong(3, network.SentCount(MT_TICK), "Streamer: unchanged symbol deduped");
      AssertTrue(network.PayloadAtContains(2, "pair=EURUSD"), "Streamer: only moved symbol re-sent");
      AssertEqualLong(3, streamer.TicksSent(), "Streamer: TicksSent aggregates symbols");
      AssertEqualLong(0, streamer.BarsSent(), "Streamer: no bars without bar times");
      AssertEqualLong(0, streamer.PartialBarsSent(), "Streamer: no partials without bar times");
   }

   //--- NULL guards: no subscriptions or no network → silent no-op
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      SubscriptionManager subscriptions(GetPointer(logger), 10, GetPointer(symbols), GetPointer(time));
      subscriptions.Add("EURUSD");

      MarketStreamer noSubs(NULL, GetPointer(network), GetPointer(symbols), GetPointer(marketData));
      noSubs.StreamAll();
      MarketStreamer noNet(GetPointer(subscriptions), NULL, GetPointer(symbols), GetPointer(marketData));
      noNet.StreamAll();

      AssertEqualLong(0, network.SentCount(), "Streamer: NULL deps send nothing");
   }
}
//+------------------------------------------------------------------+
