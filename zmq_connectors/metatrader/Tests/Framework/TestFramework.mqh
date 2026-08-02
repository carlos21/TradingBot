//+------------------------------------------------------------------+
//|                              Tests/Framework/TestFramework.mqh   |
//|  Minimal test framework — no external dependencies.              |
//|  Asserts print [PASS]/[FAIL] and accumulate into global          |
//|  counters; WriteResultsFile() mirrors the summary into           |
//|  Terminal\Common\Files so the WSL runner script can read it.     |
//+------------------------------------------------------------------+
#property strict

//+------------------------------------------------------------------+
//| TestStats — global pass/fail counters                            |
//+------------------------------------------------------------------+
struct TestStats
{
   int    passed;
   int    failed;
   string failedNames[];
};

TestStats g_testStats;

//+------------------------------------------------------------------+
//| TestResultsReset — clear counters (fresh run)                    |
//+------------------------------------------------------------------+
void TestResultsReset()
{
   g_testStats.passed = 0;
   g_testStats.failed = 0;
   ArrayResize(g_testStats.failedNames, 0);
}

//+------------------------------------------------------------------+
//| TestPassedCount / TestFailedCount                                |
//+------------------------------------------------------------------+
int TestPassedCount() { return g_testStats.passed; }
int TestFailedCount() { return g_testStats.failed; }

//+------------------------------------------------------------------+
//| Internal recorders                                               |
//+------------------------------------------------------------------+
void _RecordPass(string name)
{
   g_testStats.passed++;
   Print("[PASS] ", name);
}

void _RecordFail(string name, string detail)
{
   g_testStats.failed++;
   int n = ArraySize(g_testStats.failedNames);
   ArrayResize(g_testStats.failedNames, n + 1);
   g_testStats.failedNames[n] = name;
   Print("[FAIL] ", name, " (", detail, ")");
}

//+------------------------------------------------------------------+
//| Assert functions                                                 |
//+------------------------------------------------------------------+
void AssertTrue(bool cond, string name)
{
   if(cond) _RecordPass(name);
   else     _RecordFail(name, "expected true, got false");
}

void AssertFalse(bool cond, string name)
{
   if(!cond) _RecordPass(name);
   else      _RecordFail(name, "expected false, got true");
}

void AssertEqualLong(long expected, long actual, string name)
{
   if(expected == actual)
      _RecordPass(name);
   else
      _RecordFail(name, StringFormat("expected %I64d, got %I64d", expected, actual));
}

void AssertEqualDouble(double expected, double actual, double epsilon, string name)
{
   if(MathAbs(expected - actual) <= epsilon)
      _RecordPass(name);
   else
      _RecordFail(name, StringFormat("expected %f, got %f (eps %f)", expected, actual, epsilon));
}

void AssertEqualString(string expected, string actual, string name)
{
   if(expected == actual)
      _RecordPass(name);
   else
      _RecordFail(name, StringFormat("expected '%s', got '%s'", expected, actual));
}

void AssertStringContains(string haystack, string needle, string name)
{
   if(StringFind(haystack, needle) >= 0)
      _RecordPass(name);
   else
      _RecordFail(name, StringFormat("'%s' does not contain '%s'", haystack, needle));
}

//+------------------------------------------------------------------+
//| _SummaryLine — single machine-parseable result line              |
//+------------------------------------------------------------------+
string _SummaryLine()
{
   return StringFormat("MQLTESTS: %d passed, %d failed",
                       g_testStats.passed, g_testStats.failed);
}

//+------------------------------------------------------------------+
//| TestResultsSummary — print summary + one FAIL line per failure   |
//+------------------------------------------------------------------+
void TestResultsSummary()
{
   Print(_SummaryLine());
   for(int i = 0; i < ArraySize(g_testStats.failedNames); i++)
      Print("FAIL: ", g_testStats.failedNames[i]);
}

//+------------------------------------------------------------------+
//| WriteResultsFile — mirror summary to Terminal\Common\Files       |
//|  FILE_COMMON makes the file land in the shared Common\Files dir  |
//|  (readable from WSL); ANSI keeps parsing trivial.                |
//+------------------------------------------------------------------+
bool WriteResultsFile(string filename)
{
   int handle = FileOpen(filename, FILE_COMMON | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("WriteResultsFile: FileOpen failed, error ", GetLastError());
      return false;
   }

   FileWrite(handle, _SummaryLine());
   for(int i = 0; i < ArraySize(g_testStats.failedNames); i++)
      FileWrite(handle, "FAIL: " + g_testStats.failedNames[i]);
   FileClose(handle);
   return true;
}
