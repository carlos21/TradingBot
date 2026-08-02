//+------------------------------------------------------------------+
//|                                Application/MarketStreamer.mqh    |
//|  Streams ticks/completed bars/partial bars for every subscribed  |
//|  symbol. Driven by the EA's OnTick and OnTimer via StreamAll().  |
//|  Extracted from TradingBotZmqEA.mq5 (no behavior change).        |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/PlatformApi.mqh"
#include "SubscriptionManager.mqh"

//+------------------------------------------------------------------+
//| MarketStreamer — per-symbol tick/bar streaming                   |
//+------------------------------------------------------------------+
class MarketStreamer
{
private:
   SubscriptionManager *m_subscriptions;
   IZmqNetwork         *m_network;
   ISymbolApi          *m_symbolApi;
   IMarketDataApi      *m_marketData;

   //--- Stats
   long                 m_ticksSent;
   long                 m_barsSent;
   long                 m_partialBarsSent;

public:
   MarketStreamer(SubscriptionManager *subscriptions, IZmqNetwork *network,
                  ISymbolApi *symbolApi, IMarketDataApi *marketData)
   {
      m_subscriptions = subscriptions;
      m_network = network;
      m_symbolApi = symbolApi;
      m_marketData = marketData;
      m_ticksSent = 0;
      m_barsSent = 0;
      m_partialBarsSent = 0;
   }

   ~MarketStreamer() {}

   long TicksSent()        { return m_ticksSent; }
   long BarsSent()         { return m_barsSent; }
   long PartialBarsSent()  { return m_partialBarsSent; }

   //--- Stream ticks/bars/partials for all subscribed symbols
   void StreamAll()
   {
      if(m_subscriptions == NULL || m_network == NULL) return;

      int count = m_subscriptions.Count();
      for(int i = 0; i < count; i++)
      {
         string sym = m_subscriptions.Symbol(i);
         SendTickIfAllowed(i, sym);
         SendBarIfNew(i, sym);
      }
   }

private:
   void SendTickIfAllowed(int index, string sym)
   {
      IRateLimiter *limiter = m_subscriptions.TickLimiter(index);
      if(limiter == NULL || !limiter.TryAllow())
         return;

      MqlTick tick;
      if(!m_symbolApi.Tick(sym, tick))
         return;

      // Skip duplicates — OnTick and OnTimer both drive streaming
      if(tick.time_msc == m_subscriptions.LastTickMsc(index))
         return;
      m_subscriptions.SetLastTickMsc(index, tick.time_msc);

      double price = (tick.last > 0) ? tick.last : tick.bid;
      m_network.SendTick(sym, price, tick.volume, tick.time);
      m_ticksSent++;
   }

   void SendBarIfNew(int index, string sym)
   {
      datetime currentBarTime = m_marketData.CurrentBarTime(sym);
      if(currentBarTime == 0) return;

      datetime lastBarTime = m_subscriptions.LastBarTime(index);

      // New bar started — send the completed previous bar
      if(currentBarTime > lastBarTime && lastBarTime != 0)
      {
         MqlRates rates[1];
         if(m_marketData.CopyM1Rates(sym, 1, 1, rates) == 1)
         {
            m_network.SendBar(sym, rates[0].time, rates[0].open, rates[0].high,
                              rates[0].low, rates[0].close, rates[0].tick_volume, false);
            m_barsSent++;
         }
      }

      // Send partial (forming) bar at 1/sec rate limit
      if(lastBarTime != 0 && currentBarTime == lastBarTime)
      {
         IRateLimiter *limiter = m_subscriptions.PartialLimiter(index);
         if(limiter != NULL && limiter.TryAllow())
         {
            MqlRates rates[1];
            if(m_marketData.CopyM1Rates(sym, 0, 1, rates) == 1)
            {
               m_network.SendBar(sym, rates[0].time, rates[0].open, rates[0].high,
                                 rates[0].low, rates[0].close, rates[0].tick_volume, true);
               m_partialBarsSent++;
            }
         }
      }

      m_subscriptions.SetLastBarTime(index, currentBarTime);
   }
};
