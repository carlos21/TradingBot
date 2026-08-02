//+------------------------------------------------------------------+
//|                           Application/SubscriptionManager.mqh    |
//|  Owns the set of instruments being streamed plus per-symbol      |
//|  streaming state (rate limiters, last bar/tick tracking).        |
//|                                                                  |
//|  Source of truth after connect = subscribe/unsubscribe commands  |
//|  from Python. The EA auto-subscribes the configured pair on      |
//|  connect only as a bootstrap; Python re-drives subscribe         |
//|  commands afterwards anyway (idempotent).                        |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/PlatformApi.mqh"
#include "../Domain/TickRateLimiter.mqh"

//+------------------------------------------------------------------+
//| SubscriptionManager — subscribed symbols + their stream state    |
//+------------------------------------------------------------------+
class SubscriptionManager
{
private:
   ILogger      *m_logger;
   ISymbolApi   *m_symbolApi;
   ITimeApi     *m_timeApi;
   int           m_maxTicksPerSecond;
   string        m_symbols[];
   datetime      m_lastBarTime[];
   long          m_lastTickMsc[];
   IRateLimiter *m_tickLimiters[];
   IRateLimiter *m_partialLimiters[];

public:
   SubscriptionManager(ILogger *logger, int maxTicksPerSecond, ISymbolApi *symbolApi, ITimeApi *timeApi)
   {
      m_logger = logger;
      m_symbolApi = symbolApi;
      m_timeApi = timeApi;
      m_maxTicksPerSecond = maxTicksPerSecond;
   }

   ~SubscriptionManager()
   {
      Clear();
   }

   int Count() { return ArraySize(m_symbols); }

   bool Contains(string symbol) { return (IndexOf(symbol) >= 0); }

   //--- Subscribe: select the symbol at the broker and start tracking it.
   //--- Idempotent; returns false only when the symbol does not exist.
   bool Add(string symbol)
   {
      if(StringLen(symbol) == 0)
         return false;

      if(!m_symbolApi.Select(symbol))
      {
         if(m_logger != NULL)
            m_logger.Error("SubscriptionManager: unknown symbol '" + symbol + "'");
         return false;
      }

      if(Contains(symbol))
         return true;

      int size = ArraySize(m_symbols);
      ArrayResize(m_symbols, size + 1);
      ArrayResize(m_lastBarTime, size + 1);
      ArrayResize(m_lastTickMsc, size + 1);
      ArrayResize(m_tickLimiters, size + 1);
      ArrayResize(m_partialLimiters, size + 1);
      m_symbols[size] = symbol;
      m_lastBarTime[size] = 0;
      m_lastTickMsc[size] = 0;
      m_tickLimiters[size] = new TickRateLimiter(m_maxTicksPerSecond, m_timeApi);
      m_partialLimiters[size] = new TickRateLimiter(1, m_timeApi); // 1 partial bar/sec
      return true;
   }

   //--- Unsubscribe: stop streaming one symbol. Idempotent.
   bool Remove(string symbol)
   {
      int idx = IndexOf(symbol);
      if(idx < 0)
         return true;

      int size = ArraySize(m_symbols);
      if(m_tickLimiters[idx] != NULL)    delete m_tickLimiters[idx];
      if(m_partialLimiters[idx] != NULL) delete m_partialLimiters[idx];

      for(int i = idx; i < size - 1; i++)
      {
         m_symbols[i]         = m_symbols[i + 1];
         m_lastBarTime[i]     = m_lastBarTime[i + 1];
         m_lastTickMsc[i]     = m_lastTickMsc[i + 1];
         m_tickLimiters[i]    = m_tickLimiters[i + 1];
         m_partialLimiters[i] = m_partialLimiters[i + 1];
      }

      ArrayResize(m_symbols, size - 1);
      ArrayResize(m_lastBarTime, size - 1);
      ArrayResize(m_lastTickMsc, size - 1);
      ArrayResize(m_tickLimiters, size - 1);
      ArrayResize(m_partialLimiters, size - 1);
      return true;
   }

   void Clear()
   {
      for(int i = 0; i < ArraySize(m_tickLimiters); i++)
         if(m_tickLimiters[i] != NULL) delete m_tickLimiters[i];
      for(int i = 0; i < ArraySize(m_partialLimiters); i++)
         if(m_partialLimiters[i] != NULL) delete m_partialLimiters[i];
      ArrayResize(m_symbols, 0);
      ArrayResize(m_lastBarTime, 0);
      ArrayResize(m_lastTickMsc, 0);
      ArrayResize(m_tickLimiters, 0);
      ArrayResize(m_partialLimiters, 0);
   }

   //--- Per-symbol streaming state accessors (index < Count())
   string Symbol(int index)           { return m_symbols[index]; }
   IRateLimiter *TickLimiter(int index)    { return m_tickLimiters[index]; }
   IRateLimiter *PartialLimiter(int index) { return m_partialLimiters[index]; }
   datetime LastBarTime(int index)    { return m_lastBarTime[index]; }
   void SetLastBarTime(int index, datetime t) { m_lastBarTime[index] = t; }
   long LastTickMsc(int index)        { return m_lastTickMsc[index]; }
   void SetLastTickMsc(int index, long msc)   { m_lastTickMsc[index] = msc; }

private:
   int IndexOf(string symbol)
   {
      int size = ArraySize(m_symbols);
      for(int i = 0; i < size; i++)
         if(m_symbols[i] == symbol)
            return i;
      return -1;
   }
};
