//+------------------------------------------------------------------+
//|                                    Infrastructure/Logger.mqh     |
//|  ILogger implementation using Print() + optional UI callback.    |
//|  Pattern: Adapter (adapts MQL5 Print to ILogger interface)       |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"

// Callback for routing logs to a UI dialog
typedef void (*LogToUICallback)(string msg);

//+------------------------------------------------------------------+
//| MetaTraderLogger — prints to Experts log + optional UI callback  |
//+------------------------------------------------------------------+
class MetaTraderLogger : public ILogger
{
private:
   string m_prefix;
   string m_lastStatus;
   LogToUICallback m_uiCallback;

public:
   MetaTraderLogger(string prefix = "[ZMQ]")
   {
      m_prefix = prefix;
      m_lastStatus = "";
      m_uiCallback = NULL;
   }

   ~MetaTraderLogger() {}

   void SetUICallback(LogToUICallback cb)
   {
      m_uiCallback = cb;
   }

   //--- ILogger implementation
   void Info(string msg) override
   {
      Print(m_prefix, " [INFO] ", msg);
      if(m_uiCallback != NULL) m_uiCallback("[INFO] " + msg);
   }

   void Warning(string msg) override
   {
      Print(m_prefix, " [WARN] ", msg);
      if(m_uiCallback != NULL) m_uiCallback("[WARN] " + msg);
   }

   void Error(string msg) override
   {
      Print(m_prefix, " [ERROR] ", msg);
      if(m_uiCallback != NULL) m_uiCallback("[ERROR] " + msg);
   }

   void Success(string msg) override
   {
      Print(m_prefix, " [OK] ", msg);
      if(m_uiCallback != NULL) m_uiCallback("[OK] " + msg);
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
