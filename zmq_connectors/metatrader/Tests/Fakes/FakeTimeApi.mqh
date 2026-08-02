//+------------------------------------------------------------------+
//|                                  Tests/Fakes/FakeTimeApi.mqh     |
//|  Fake ITimeApi — settable/advanceable clocks.                    |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeTimeApi — scripted time                                      |
//+------------------------------------------------------------------+
class FakeTimeApi : public ITimeApi
{
private:
   long     m_tickCountMs;
   datetime m_now;

public:
   FakeTimeApi()
   {
      m_tickCountMs = 0;
      m_now = 0;
   }

   void SetTickCount(long ms)  { m_tickCountMs = ms; }
   void SetNow(datetime now)   { m_now = now; }
   void Advance(long ms)       { m_tickCountMs += ms; m_now += (datetime)(ms / 1000); }
   void AdvanceSeconds(int s)  { Advance((long)s * 1000); }

   long     TickCount() override { return m_tickCountMs; }
   datetime Now() override       { return m_now; }
};
