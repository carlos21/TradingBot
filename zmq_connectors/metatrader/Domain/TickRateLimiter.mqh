//+------------------------------------------------------------------+
//|                                   Domain/TickRateLimiter.mqh     |
//|  Simple time-window rate limiter.                                |
//|  Pattern: Strategy (IRateLimiter implementation)                 |
//+------------------------------------------------------------------+
#property strict

#include "Contracts.mqh"
#include "PlatformApi.mqh"

//+------------------------------------------------------------------+
//| TickRateLimiter — allows N events per second                     |
//+------------------------------------------------------------------+
class TickRateLimiter : public IRateLimiter
{
private:
   ITimeApi *m_time;
   int       m_maxPerSecond;
   int       m_count;
   uint      m_lastResetMs;

public:
   TickRateLimiter(int maxPerSecond, ITimeApi *timeApi)
   {
      m_time = timeApi;
      m_maxPerSecond = maxPerSecond > 0 ? maxPerSecond : 1;
      m_count = 0;
      m_lastResetMs = (uint)m_time.TickCount();
   }

   ~TickRateLimiter() {}

   //--- IRateLimiter implementation
   bool TryAllow() override
   {
      uint nowMs = (uint)m_time.TickCount();
      if(nowMs - m_lastResetMs >= 1000)
      {
         m_lastResetMs = nowMs;
         m_count = 0;
      }
      if(m_count >= m_maxPerSecond)
         return false;

      m_count++;
      return true;
   }

   void Reset() override
   {
      m_count = 0;
      m_lastResetMs = (uint)m_time.TickCount();
   }
};
