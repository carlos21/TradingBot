//+------------------------------------------------------------------+
//|                     Tests/Suites/TestSubscriptionManager.mqh     |
//|  Suite: SubscriptionManager add/remove/idempotency, symbol       |
//|  ordering, per-symbol tick/bar state, and per-symbol rate        |
//|  limiters (driven by FakeSymbolApi / FakeTimeApi).               |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Application/SubscriptionManager.mqh"

//+------------------------------------------------------------------+
//| RunSubscriptionManagerTests                                      |
//+------------------------------------------------------------------+
void RunSubscriptionManagerTests()
{
   //--- Add / Count / Contains / Symbol ordering
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));

      AssertEqualLong(0, subs.Count(), "Subs: starts empty");
      AssertFalse(subs.Contains("EURUSD"), "Subs: not contained before add");
      AssertTrue(subs.Add("EURUSD"), "Subs: add EURUSD succeeds");
      AssertTrue(subs.Add("GBPUSD"), "Subs: add GBPUSD succeeds");
      AssertTrue(subs.Add("USDJPY"), "Subs: add USDJPY succeeds");
      AssertEqualLong(3, subs.Count(), "Subs: count after three adds");
      AssertTrue(subs.Contains("GBPUSD"), "Subs: contains added symbol");
      AssertFalse(subs.Contains("AUDUSD"), "Subs: does not contain other symbol");
      AssertEqualString("EURUSD", subs.Symbol(0), "Subs: symbol order 0");
      AssertEqualString("GBPUSD", subs.Symbol(1), "Subs: symbol order 1");
      AssertEqualString("USDJPY", subs.Symbol(2), "Subs: symbol order 2");
   }

   //--- Add is idempotent for an already-tracked symbol
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));

      AssertTrue(subs.Add("EURUSD"), "Subs: first add succeeds");
      AssertTrue(subs.Add("EURUSD"), "Subs: duplicate add still succeeds");
      AssertEqualLong(1, subs.Count(), "Subs: duplicate add does not grow count");
   }

   //--- Add rejects an empty symbol
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));

      AssertFalse(subs.Add(""), "Subs: empty symbol rejected");
      AssertEqualLong(0, subs.Count(), "Subs: empty symbol not tracked");
   }

   //--- Add rejects a symbol the broker does not know
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      symbols.SetSelectResult("NOPE", false);
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));

      AssertFalse(subs.Add("NOPE"), "Subs: unknown symbol rejected");
      AssertEqualLong(0, subs.Count(), "Subs: unknown symbol not tracked");
      AssertTrue(logger.Contains("unknown symbol 'NOPE'"), "Subs: unknown symbol logs error");
   }

   //--- Per-symbol tick/bar state is independent
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");
      subs.Add("GBPUSD");

      AssertEqualLong(0, subs.LastTickMsc(0), "Subs: tick msc starts at zero");
      AssertEqualLong(0, (long)subs.LastBarTime(0), "Subs: bar time starts at zero");

      subs.SetLastTickMsc(0, 111);
      subs.SetLastTickMsc(1, 222);
      AssertEqualLong(111, subs.LastTickMsc(0), "Subs: tick msc set symbol 0");
      AssertEqualLong(222, subs.LastTickMsc(1), "Subs: tick msc set symbol 1");

      datetime t0 = (datetime)1700000000;
      datetime t1 = (datetime)1700000600;
      subs.SetLastBarTime(0, t0);
      subs.SetLastBarTime(1, t1);
      AssertEqualLong((long)t0, (long)subs.LastBarTime(0), "Subs: bar time set symbol 0");
      AssertEqualLong((long)t1, (long)subs.LastBarTime(1), "Subs: bar time set symbol 1");
   }

   //--- Limiters exist, differ per symbol/kind, enforce their rates
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");
      subs.Add("GBPUSD");

      AssertTrue(subs.TickLimiter(0) != NULL, "Subs: tick limiter exists symbol 0");
      AssertTrue(subs.TickLimiter(1) != NULL, "Subs: tick limiter exists symbol 1");
      AssertTrue(subs.PartialLimiter(0) != NULL, "Subs: partial limiter exists symbol 0");
      AssertTrue(subs.PartialLimiter(1) != NULL, "Subs: partial limiter exists symbol 1");
      AssertTrue(subs.TickLimiter(0) != subs.TickLimiter(1), "Subs: tick limiters independent per symbol");
      AssertTrue(subs.TickLimiter(0) != subs.PartialLimiter(0), "Subs: tick and partial limiters differ");

      //--- Tick limiter honors maxTicksPerSecond = 2
      AssertTrue(subs.TickLimiter(0).TryAllow(), "Subs: tick limiter call 1 allowed");
      AssertTrue(subs.TickLimiter(0).TryAllow(), "Subs: tick limiter call 2 allowed");
      AssertFalse(subs.TickLimiter(0).TryAllow(), "Subs: tick limiter call 3 blocked");

      //--- Symbol 1 limiter unaffected by symbol 0 usage
      AssertTrue(subs.TickLimiter(1).TryAllow(), "Subs: symbol 1 tick limiter independent");

      //--- Partial limiter is fixed at 1 per second
      AssertTrue(subs.PartialLimiter(0).TryAllow(), "Subs: partial limiter call 1 allowed");
      AssertFalse(subs.PartialLimiter(0).TryAllow(), "Subs: partial limiter call 2 blocked");

      //--- Both reopen after the 1-second window
      time.Advance(1001);
      AssertTrue(subs.TickLimiter(0).TryAllow(), "Subs: tick limiter resets after window");
      AssertTrue(subs.PartialLimiter(0).TryAllow(), "Subs: partial limiter resets after window");
   }

   //--- Remove shifts the array and keeps state attached to its symbol
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");
      subs.Add("GBPUSD");
      subs.Add("USDJPY");
      subs.SetLastTickMsc(2, 999);

      AssertTrue(subs.Remove("GBPUSD"), "Subs: remove middle symbol succeeds");
      AssertEqualLong(2, subs.Count(), "Subs: count after remove");
      AssertFalse(subs.Contains("GBPUSD"), "Subs: removed symbol gone");
      AssertEqualString("EURUSD", subs.Symbol(0), "Subs: order kept before removed slot");
      AssertEqualString("USDJPY", subs.Symbol(1), "Subs: later symbol shifted down");
      AssertEqualLong(999, subs.LastTickMsc(1), "Subs: state travels with shifted symbol");
      AssertTrue(subs.TickLimiter(1) != NULL, "Subs: shifted symbol keeps a limiter");
   }

   //--- Remove is idempotent for unknown symbols
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");

      AssertTrue(subs.Remove("AUDUSD"), "Subs: removing unknown symbol succeeds");
      AssertEqualLong(1, subs.Count(), "Subs: unknown remove keeps count");
   }

   //--- Remove then re-add resets the per-symbol state
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 1, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");
      subs.SetLastTickMsc(0, 555);
      subs.SetLastBarTime(0, (datetime)1700000000);
      AssertTrue(subs.TickLimiter(0).TryAllow(), "Subs: limiter used before remove");

      AssertTrue(subs.Remove("EURUSD"), "Subs: remove succeeds");
      AssertTrue(subs.Add("EURUSD"), "Subs: re-add succeeds");
      AssertEqualLong(0, subs.LastTickMsc(0), "Subs: re-add resets tick msc");
      AssertEqualLong(0, (long)subs.LastBarTime(0), "Subs: re-add resets bar time");
      AssertTrue(subs.TickLimiter(0).TryAllow(), "Subs: re-add gets a fresh limiter");
   }

   //--- Clear empties everything
   {
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 2, GetPointer(symbols), GetPointer(time));
      subs.Add("EURUSD");
      subs.Add("GBPUSD");

      subs.Clear();
      AssertEqualLong(0, subs.Count(), "Subs: count zero after clear");
      AssertFalse(subs.Contains("EURUSD"), "Subs: nothing contained after clear");
   }
}
//+------------------------------------------------------------------+
