//+------------------------------------------------------------------+
//|                          Tests/Suites/TestTickRateLimiter.mqh    |
//|  Suite: TickRateLimiter allows N events per second, resets on    |
//|  the 1-second window boundary (driven by FakeTimeApi).           |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Domain/TickRateLimiter.mqh"

//+------------------------------------------------------------------+
//| RunTickRateLimiterTests                                          |
//+------------------------------------------------------------------+
void RunTickRateLimiterTests()
{
   //--- Allows up to maxPerSecond, then blocks
   {
      FakeTimeApi time;
      TickRateLimiter limiter(3, &time);

      AssertTrue(limiter.TryAllow(), "RateLimiter: 1st call allowed");
      AssertTrue(limiter.TryAllow(), "RateLimiter: 2nd call allowed");
      AssertTrue(limiter.TryAllow(), "RateLimiter: 3rd call allowed");
      AssertFalse(limiter.TryAllow(), "RateLimiter: 4th call blocked");
   }

   //--- Window resets after 1000ms
   {
      FakeTimeApi time;
      TickRateLimiter limiter(2, &time);

      AssertTrue(limiter.TryAllow(), "RateLimiter: window1 call1 allowed");
      AssertTrue(limiter.TryAllow(), "RateLimiter: window1 call2 allowed");
      AssertFalse(limiter.TryAllow(), "RateLimiter: window1 call3 blocked");

      time.Advance(1001);
      AssertTrue(limiter.TryAllow(), "RateLimiter: window2 call1 allowed after reset");
   }

   //--- Sub-second advance does NOT reset
   {
      FakeTimeApi time;
      TickRateLimiter limiter(1, &time);

      AssertTrue(limiter.TryAllow(), "RateLimiter: call allowed");
      time.Advance(999);
      AssertFalse(limiter.TryAllow(), "RateLimiter: still blocked after 999ms");
   }

   //--- Reset() reopens the window
   {
      FakeTimeApi time;
      TickRateLimiter limiter(1, &time);

      AssertTrue(limiter.TryAllow(), "RateLimiter: call allowed before reset");
      AssertFalse(limiter.TryAllow(), "RateLimiter: blocked before reset");
      limiter.Reset();
      AssertTrue(limiter.TryAllow(), "RateLimiter: allowed after Reset()");
   }

   //--- Non-positive maxPerSecond is clamped to 1
   {
      FakeTimeApi time;
      TickRateLimiter limiter(0, &time);

      AssertTrue(limiter.TryAllow(), "RateLimiter: clamped max allows one");
      AssertFalse(limiter.TryAllow(), "RateLimiter: clamped max blocks second");
   }
}
