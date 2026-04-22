//+------------------------------------------------------------------+
//|                                    Infrastructure/Logger.mqh     |
//|  ILogger implementation using Print() + chart Comment().         |
//|  Pattern: Adapter (adapts MQL5 Print to ILogger interface)       |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"

//+------------------------------------------------------------------+
//| MetaTraderLogger — prints to Experts log + chart comment         |
//+------------------------------------------------------------------+
class MetaTraderLogger : public ILogger
{
private:
   string m_prefix;
   string m_lastStatus;

public:
   MetaTraderLogger(string prefix = "[ZMQ]")
   {
      m_prefix = prefix;
      m_lastStatus = "";
   }

   ~MetaTraderLogger() {}

   //--- ILogger implementation
   void Info(string msg) override
   {
      Print(m_prefix, " [INFO] ", msg);
   }

   void Warning(string msg) override
   {
      Print(m_prefix, " [WARN] ", msg);
   }

   void Error(string msg) override
   {
      Print(m_prefix, " [ERROR] ", msg);
   }

   void Success(string msg) override
   {
      Print(m_prefix, " [OK] ", msg);
   }

   //--- Update chart comment panel (simple status display)
   void UpdatePanel(string status)
   {
      m_lastStatus = status;
      Comment(m_prefix, "\n", status);
   }

   string GetLastStatus() const
   {
      return m_lastStatus;
   }
};
