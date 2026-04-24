//+------------------------------------------------------------------+
//|                                   Domain/TickRateLimiter.mqh     |
//|  Simple time-window rate limiter.                                |
//|  Pattern: Strategy (IRateLimiter implementation)                 |
//+------------------------------------------------------------------+
#property strict

#include "Contracts.mqh"

//+------------------------------------------------------------------+
//| TickRateLimiter — allows N events per second                     |
//+------------------------------------------------------------------+
class TickRateLimiter : public IRateLimiter
{
private:
   int    m_maxPerSecond;
   int    m_count;
   uint   m_lastResetMs;

public:
   TickRateLimiter(int maxPerSecond)
   {
      m_maxPerSecond = maxPerSecond > 0 ? maxPerSecond : 1;
      m_count = 0;
      m_lastResetMs = GetTickCount();
   }

   ~TickRateLimiter() {}

   //--- IRateLimiter implementation
   bool TryAllow() override
   {
      uint nowMs = GetTickCount();
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
      m_lastResetMs = GetTickCount();
   }
};
