//+------------------------------------------------------------------+
//|                                   Tests/SmokeTests.mqh           |
//|  Framework self-check — proves the harness runs and counts.      |
//+------------------------------------------------------------------+
#property strict

#include "Framework/TestFramework.mqh"

//+------------------------------------------------------------------+
//| RunSmokeTests — one passing assert per assert family             |
//+------------------------------------------------------------------+
void RunSmokeTests()
{
   AssertTrue(true, "Smoke: AssertTrue passes");
   AssertFalse(false, "Smoke: AssertFalse passes");
   AssertEqualLong(42, 42, "Smoke: AssertEqualLong passes");
   AssertEqualDouble(1.23456, 1.23457, 0.0001, "Smoke: AssertEqualDouble passes");
   AssertEqualString("abc", "abc", "Smoke: AssertEqualString passes");
   AssertStringContains("hello world", "world", "Smoke: AssertStringContains passes");
}
