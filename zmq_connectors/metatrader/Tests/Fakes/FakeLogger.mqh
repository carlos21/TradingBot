//+------------------------------------------------------------------+
//|                                  Tests/Fakes/FakeLogger.mqh      |
//|  Fake ILogger — stores every message for later assertions.       |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/Contracts.mqh"

//+------------------------------------------------------------------+
//| FakeLogger — in-memory ILogger for tests                         |
//+------------------------------------------------------------------+
class FakeLogger : public ILogger
{
private:
   string m_messages[];

   void _Store(string level, string msg)
   {
      int n = ArraySize(m_messages);
      ArrayResize(m_messages, n + 1);
      m_messages[n] = level + ": " + msg;
   }

public:
   void Info(string msg) override    { _Store("INFO", msg); }
   void Warning(string msg) override { _Store("WARN", msg); }
   void Error(string msg) override   { _Store("ERROR", msg); }
   void Success(string msg) override { _Store("OK", msg); }
   void Debug(string msg) override   { _Store("DEBUG", msg); }

   int Count() { return ArraySize(m_messages); }

   string MessageAt(int index)
   {
      if(index < 0 || index >= ArraySize(m_messages)) return "";
      return m_messages[index];
   }

   string LastMessage()
   {
      int n = ArraySize(m_messages);
      return n > 0 ? m_messages[n - 1] : "";
   }

   bool Contains(string substr)
   {
      for(int i = 0; i < ArraySize(m_messages); i++)
         if(StringFind(m_messages[i], substr) >= 0)
            return true;
      return false;
   }

   void Clear() { ArrayResize(m_messages, 0); }
};
