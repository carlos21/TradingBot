//+------------------------------------------------------------------+
//|                     Tests/Suites/TestHistoryProvider.mqh         |
//|  Suite: HistoryProvider — refresh_start → batches → history_end  |
//|  protocol, batch chunking, bar JSON fields, days resolution,     |
//|  symbol override, empty/partial history.                         |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Application/HistoryProvider.mqh"

//+------------------------------------------------------------------+
//| _HistScriptRates — fill a series-ordered (index 0 = newest)      |
//|  rates array with deterministic, index-derived values.           |
//+------------------------------------------------------------------+
void _HistScriptRates(int count, MqlRates &rates[])
{
   ArrayResize(rates, count);
   for(int i = 0; i < count; i++)
   {
      rates[i].time        = (datetime)(1700000000 + (count - 1 - i) * 60);
      rates[i].open        = 1.10000 + i * 0.00001;
      rates[i].high        = rates[i].open + 0.00050;
      rates[i].low         = rates[i].open - 0.00050;
      rates[i].close       = rates[i].open + 0.00020;
      rates[i].tick_volume = 1000 + i;
      rates[i].spread      = 0;
      rates[i].real_volume = 0;
   }
}

//+------------------------------------------------------------------+
//| _HistCountOccurrences — count non-overlapping substring hits     |
//+------------------------------------------------------------------+
int _HistCountOccurrences(string haystack, string needle)
{
   int count = 0;
   int pos = 0;
   while(true)
   {
      pos = StringFind(haystack, needle, pos);
      if(pos < 0) break;
      count++;
      pos += StringLen(needle);
   }
   return count;
}

//+------------------------------------------------------------------+
//| RunHistoryProviderTests                                          |
//+------------------------------------------------------------------+
void RunHistoryProviderTests()
{
   //--- Protocol order + batch chunking (batchSize 3 clamps to 10)
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(25, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(25);

      ZmqConfiguration config;
      config.batchSize = 3;      //--- clamped to 10 by the provider
      config.historyDays = 2;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "EURUSD");

      AssertEqualLong(5, network.SentCount(), "History: refresh_start + 3 batches + history_end");
      AssertEqualString(MT_REFRESH_START, network.MsgTypeAt(0), "History: refresh_start first");
      AssertEqualString(MT_HISTORY_BATCH, network.MsgTypeAt(1), "History: batch 1 second");
      AssertEqualString(MT_HISTORY_BATCH, network.MsgTypeAt(2), "History: batch 2 third");
      AssertEqualString(MT_HISTORY_BATCH, network.MsgTypeAt(3), "History: batch 3 fourth");
      AssertEqualString(MT_HISTORY_END, network.MsgTypeAt(4), "History: history_end last");
      AssertEqualLong(3, network.SentCount(MT_HISTORY_BATCH), "History: 25 bars in chunks of 10/10/5");
   }

   //--- Bar JSON fields in the raw batch payload
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(25, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(25);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 2;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "EURUSD");

      //--- First batch = series indices 15..24 (oldest bars); its first
      //--- bar is index 15: time 1700000000 + 9*60, open 1.10015, ...
      string batch1 = network.PayloadAt(1);
      AssertTrue(StringFind(batch1, "pair=EURUSD") >= 0, "History: batch carries pair");
      AssertTrue(StringFind(batch1, "days=2") >= 0, "History: batch carries days");
      AssertStringContains(batch1, "{\"time\":1700000540", "History: bar time field");
      AssertStringContains(batch1, "\"open\":1.10015", "History: bar open field");
      AssertStringContains(batch1, "\"high\":1.10065", "History: bar high field");
      AssertStringContains(batch1, "\"low\":1.09965", "History: bar low field");
      AssertStringContains(batch1, "\"close\":1.10035", "History: bar close field");
      AssertStringContains(batch1, "\"volume\":1015", "History: bar volume field");
      AssertStringContains(batch1, "\"pair\":\"EURUSD\"", "History: bar pair field");
      AssertEqualLong(10, _HistCountOccurrences(batch1, "{\"time\""), "History: batch 1 holds 10 bars");
      AssertEqualLong(5, _HistCountOccurrences(network.PayloadAt(3), "{\"time\""), "History: last batch holds 5 bars");
   }

   //--- Explicit days parameter lands in every batch
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(12, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(12);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 2;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(5, "EURUSD");

      AssertEqualLong(2, network.SentCount(MT_HISTORY_BATCH), "History: 12 bars in chunks of 10/2");
      AssertTrue(StringFind(network.PayloadAt(1), "days=5") >= 0, "History: explicit days in batch 1");
      AssertTrue(StringFind(network.PayloadAt(2), "days=5") >= 0, "History: explicit days in batch 2");
   }

   //--- days=0 falls back to the configured historyDays
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(3, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(3);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 2;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "EURUSD");

      AssertTrue(StringFind(network.PayloadAt(1), "days=2") >= 0, "History: config default days used");
   }

   //--- The requested symbol is the pair in every protocol message
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(3, rates);
      marketData.SetRates("GBPUSD", rates);
      marketData.SetBarsCount(3);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 1;

      //--- Constructed for EURUSD but the request overrides with GBPUSD
      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "GBPUSD");

      AssertTrue(StringFind(network.PayloadAt(0), "pair=GBPUSD") >= 0, "History: refresh_start uses requested symbol");
      AssertStringContains(network.PayloadAt(1), "\"pair\":\"GBPUSD\"", "History: bars tagged requested symbol");
      AssertTrue(StringFind(network.PayloadAt(2), "pair=GBPUSD") >= 0, "History: history_end uses requested symbol");
   }

   //--- Empty symbol falls back to the configured pair
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(3, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(3);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 1;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "");

      AssertTrue(StringFind(network.PayloadAt(0), "pair=EURUSD") >= 0, "History: empty symbol falls back to config pair");
   }

   //--- Empty history → refresh_start + history_end only
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);
      marketData.SetBarsCount(0);

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 1;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "EURUSD");

      AssertEqualLong(2, network.SentCount(), "History: empty history sends start+end only");
      AssertEqualString(MT_REFRESH_START, network.MsgTypeAt(0), "History: empty history starts with refresh_start");
      AssertEqualString(MT_HISTORY_END, network.MsgTypeAt(1), "History: empty history ends with history_end");
      AssertTrue(logger.Contains("Bars() returned 0"), "History: empty history logged");
   }

   //--- Bars() over-reports → partial copy still completes cleanly
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      MqlRates rates[];
      _HistScriptRates(3, rates);
      marketData.SetRates("EURUSD", rates);
      marketData.SetBarsCount(5);   //--- claims 5, only 3 available

      ZmqConfiguration config;
      config.batchSize = 10;
      config.historyDays = 1;

      HistoryProvider provider(GetPointer(network), GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(0, "EURUSD");

      AssertEqualLong(3, network.SentCount(), "History: partial copy sends one batch");
      AssertEqualLong(3, _HistCountOccurrences(network.PayloadAt(1), "{\"time\""), "History: partial batch holds 3 bars");
      AssertEqualString(MT_HISTORY_END, network.MsgTypeAt(2), "History: partial copy still ends protocol");
   }

   //--- NULL network → immediate return, nothing logged
   {
      FakeLogger logger;
      FakeMarketDataApi marketData;
      FakeTimeApi time;
      time.SetNow((datetime)1700100000);

      ZmqConfiguration config;
      HistoryProvider provider(NULL, GetPointer(logger), GetPointer(config),
                               "EURUSD", GetPointer(marketData), GetPointer(time));
      provider.SendHistory(1, "EURUSD");

      AssertEqualLong(0, logger.Count(), "History: NULL network returns before logging");
   }
}
//+------------------------------------------------------------------+
