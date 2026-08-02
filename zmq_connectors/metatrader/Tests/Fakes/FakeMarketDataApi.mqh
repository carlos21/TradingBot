//+------------------------------------------------------------------+
//|                              Tests/Fakes/FakeMarketDataApi.mqh   |
//|  Fake IMarketDataApi — scripted M1 rate arrays per symbol.       |
//|  Rates are stored in series order (index 0 = most recent),       |
//|  matching CopyRates start_pos semantics.                         |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeMarketDataApi — scripted bar data                            |
//+------------------------------------------------------------------+
class FakeMarketDataApi : public IMarketDataApi
{
private:
   string   m_symbols[];
   MqlRates m_rates[];      //--- flat per-symbol block: m_offsets[i]..+m_counts[i]
   int      m_offsets[];
   int      m_counts[];
   datetime m_barTimes[];
   bool     m_hasBarTime[];
   int      m_barsCount;    //--- canned BarsCount return

   int _Find(string sym)
   {
      for(int i = 0; i < ArraySize(m_symbols); i++)
         if(m_symbols[i] == sym)
            return i;
      return -1;
   }

public:
   FakeMarketDataApi()
   {
      m_barsCount = 0;
   }

   //--- Test hooks -------------------------------------------------
   void SetRates(string sym, MqlRates &rates[])
   {
      int idx = _Find(sym);
      if(idx < 0)
      {
         idx = ArraySize(m_symbols);
         ArrayResize(m_symbols, idx + 1);
         ArrayResize(m_offsets, idx + 1);
         ArrayResize(m_counts, idx + 1);
         ArrayResize(m_barTimes, idx + 1);
         ArrayResize(m_hasBarTime, idx + 1);
         m_symbols[idx] = sym;
         m_offsets[idx] = 0;
         m_counts[idx] = 0;
         m_hasBarTime[idx] = false;
      }
      m_offsets[idx] = ArraySize(m_rates);
      m_counts[idx] = ArraySize(rates);
      int n = ArraySize(m_rates);
      ArrayResize(m_rates, n + ArraySize(rates));
      for(int i = 0; i < ArraySize(rates); i++)
         m_rates[n + i] = rates[i];
   }

   void SetCurrentBarTime(string sym, datetime barTime)
   {
      int idx = _Find(sym);
      if(idx < 0)
      {
         MqlRates empty[];
         SetRates(sym, empty);
         idx = _Find(sym);
      }
      m_barTimes[idx] = barTime;
      m_hasBarTime[idx] = true;
   }

   void SetBarsCount(int count) { m_barsCount = count; }

   //--- IMarketDataApi ---------------------------------------------
   datetime CurrentBarTime(string sym) override
   {
      int idx = _Find(sym);
      if(idx < 0 || !m_hasBarTime[idx])
         return 0;
      return m_barTimes[idx];
   }

   int CopyM1Rates(string sym, int startPos, int count, MqlRates &rates[]) override
   {
      int idx = _Find(sym);
      if(idx < 0 || startPos >= m_counts[idx])
         return 0;
      int available = m_counts[idx] - startPos;
      int copied = MathMin(count, available);
      ArrayResize(rates, copied);
      for(int i = 0; i < copied; i++)
         rates[i] = m_rates[m_offsets[idx] + startPos + i];
      return copied;
   }

   int BarsCount(string sym, datetime start, datetime end) override
   {
      return m_barsCount;
   }
};
