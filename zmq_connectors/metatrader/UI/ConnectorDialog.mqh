//+------------------------------------------------------------------+
//|                                        UI/ConnectorDialog.mqh    |
//|  MQL5 dialog UI for TradingBot ZMQ Connector                     |
//|  Mirrors NinjaTrader ZmqConnectorWindow functionality            |
//+------------------------------------------------------------------+
#property strict

#include <Controls/Dialog.mqh>
#include <Controls/Button.mqh>
#include <Controls/Label.mqh>
#include <Controls/CheckBox.mqh>

// Callback typedef for button clicks (global scope)
typedef void (*ButtonCallback)(void);

//+------------------------------------------------------------------+
//| CConnectorDialog — compact MQL5 dialog for ZMQ connector         |
//+------------------------------------------------------------------+
class CConnectorDialog : public CAppDialog
{
private:
   CLabel        m_lblStatus;
   CLabel        m_lblStats;
   CButton       m_btnConnect;
   CButton       m_btnTest;
   CButton       m_btnE2E;
   CCheckBox     m_chkSimulate;
   
   ButtonCallback m_onConnect;
   ButtonCallback m_onTest;
   ButtonCallback m_onE2E;
   
   // Callback for simulate checkbox state change: param = new checked state
   typedef void (*SimulateCallback)(bool);
   
   bool          m_testEnabled;
   bool          m_e2eEnabled;

public:
                     CConnectorDialog(void);
                    ~CConnectorDialog(void);
   
   bool              Create(const long chart, const string name, const int subwin, 
                            const int x1, const int y1, const int x2, const int y2);
   
   void              SetHandlers(ButtonCallback onConnect, ButtonCallback onTest, ButtonCallback onE2E);
   bool              IsSimulateChecked(void);
   
   // Event handler — standard Controls library pattern
   virtual bool      OnEvent(const int id, const long &lparam, const double &dparam, const string &sparam);
   
   void              UpdateStatus(bool connected, string statsText);
   void              SetButtonEnabled(int btnIndex, bool enabled);
   void              SetSimulateChecked(bool checked);

protected:
   bool              CreateStatusLabel(void);
   bool              CreateStatsLabel(void);
   bool              CreateButtons(void);
   bool              CreateSimulateCheckbox(void);
};

//+------------------------------------------------------------------+
CConnectorDialog::CConnectorDialog(void)
{
   m_onConnect = NULL;
   m_onTest = NULL;
   m_onE2E = NULL;
   m_testEnabled = false;
   m_e2eEnabled = false;
}

//+------------------------------------------------------------------+
CConnectorDialog::~CConnectorDialog(void)
{
}

//+------------------------------------------------------------------+
bool CConnectorDialog::Create(const long chart, const string name, const int subwin, 
                              const int x1, const int y1, const int x2, const int y2)
{
   if(!CAppDialog::Create(chart, name, subwin, x1, y1, x2, y2))
      return false;
   
   if(!CreateStatusLabel()) return false;
   if(!CreateStatsLabel()) return false;
   if(!CreateButtons()) return false;
   if(!CreateSimulateCheckbox()) return false;
   
   if(!Run())
      return false;
   
   return true;
}

//+------------------------------------------------------------------+
bool CConnectorDialog::CreateStatusLabel(void)
{
   int x1 = ClientAreaLeft() + 10;
   int y1 = ClientAreaTop() + 10;
   int x2 = ClientAreaRight() - 10;
   int y2 = y1 + 20;
   
   if(!m_lblStatus.Create(0, m_name + "Status", 0, x1, y1, x2, y2))
      return false;
   if(!Add(m_lblStatus)) return false;
   
   m_lblStatus.Text("DISCONNECTED");
   m_lblStatus.Color(clrOrangeRed);
   
   return true;
}

//+------------------------------------------------------------------+
bool CConnectorDialog::CreateStatsLabel(void)
{
   int x1 = ClientAreaLeft() + 10;
   int y1 = ClientAreaTop() + 34;
   int x2 = ClientAreaRight() - 10;
   int y2 = y1 + 16;
   
   if(!m_lblStats.Create(0, m_name + "Stats", 0, x1, y1, x2, y2))
      return false;
   if(!Add(m_lblStats)) return false;
   
   m_lblStats.Text("ZeroMQ Edition - High Performance");
   m_lblStats.Color(clrSilver);
   
   return true;
}

//+------------------------------------------------------------------+
bool CConnectorDialog::CreateButtons(void)
{
   int x1 = ClientAreaLeft() + 10;
   int y1 = ClientAreaTop() + 58;
   int btnW = 120;
   int btnH = 28;
   int gap = 8;
   
   // Connect button
   if(!m_btnConnect.Create(0, m_name + "BtnConnect", 0, x1, y1, x1 + btnW, y1 + btnH))
      return false;
   if(!Add(m_btnConnect)) return false;
   m_btnConnect.Text("Connect");
   
   // Test button
   x1 += btnW + gap;
   if(!m_btnTest.Create(0, m_name + "BtnTest", 0, x1, y1, x1 + btnW, y1 + btnH))
      return false;
   if(!Add(m_btnTest)) return false;
   m_btnTest.Text("Test Connection");
   m_btnTest.Color(clrGray);
   m_testEnabled = false;
   
   // E2E button
   x1 += btnW + gap;
   if(!m_btnE2E.Create(0, m_name + "BtnE2E", 0, x1, y1, x1 + btnW, y1 + btnH))
      return false;
   if(!Add(m_btnE2E)) return false;
   m_btnE2E.Text("Run E2E Tests");
   m_btnE2E.Color(clrGray);
   m_e2eEnabled = false;
   
   return true;
}

//+------------------------------------------------------------------+
bool CConnectorDialog::CreateSimulateCheckbox(void)
{
   int x1 = ClientAreaLeft() + 10;
   int y1 = ClientAreaTop() + 92;
   int x2 = ClientAreaRight() - 10;
   int y2 = y1 + 20;
   
   if(!m_chkSimulate.Create(0, m_name + "ChkSimulate", 0, x1, y1, x2, y2))
      return false;
   if(!Add(m_chkSimulate)) return false;
   
   m_chkSimulate.Text("Simulate Trades (No Orders)");
   m_chkSimulate.Color(clrSilver);
   
   return true;
}

//+------------------------------------------------------------------+
void CConnectorDialog::SetHandlers(ButtonCallback onConnect, ButtonCallback onTest, ButtonCallback onE2E)
{
   m_onConnect = onConnect;
   m_onTest = onTest;
   m_onE2E = onE2E;
}

//+------------------------------------------------------------------+
bool CConnectorDialog::OnEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_CUSTOM + ON_CLICK)
   {
      if(lparam == m_btnConnect.Id())
      {
         if(m_onConnect != NULL) m_onConnect();
         return true;
      }
      if(lparam == m_btnTest.Id())
      {
         if(m_testEnabled && m_onTest != NULL) m_onTest();
         return true;
      }
      if(lparam == m_btnE2E.Id())
      {
         if(m_e2eEnabled && m_onE2E != NULL) m_onE2E();
         return true;
      }
   }
   return CAppDialog::OnEvent(id, lparam, dparam, sparam);
}

//+------------------------------------------------------------------+
void CConnectorDialog::UpdateStatus(bool connected, string statsText)
{
   if(connected)
   {
      m_lblStatus.Text("CONNECTED");
      m_lblStatus.Color(clrLimeGreen);
      m_btnConnect.Text("Disconnect");
      m_btnTest.Color(clrBlack);
      m_btnE2E.Color(clrBlack);
      m_testEnabled = true;
      m_e2eEnabled = true;
   }
   else
   {
      m_lblStatus.Text("DISCONNECTED");
      m_lblStatus.Color(clrOrangeRed);
      m_btnConnect.Text("Connect");
      m_btnTest.Color(clrGray);
      m_btnE2E.Color(clrGray);
      m_testEnabled = false;
      m_e2eEnabled = false;
   }
   
   if(StringLen(statsText) > 0)
      m_lblStats.Text(statsText);
}

//+------------------------------------------------------------------+
void CConnectorDialog::SetButtonEnabled(int btnIndex, bool enabled)
{
   if(btnIndex == 0)
   {
      // Connect button is always visually enabled; it toggles state
   }
   if(btnIndex == 1)
   {
      m_testEnabled = enabled;
      m_btnTest.Color(enabled ? clrBlack : clrGray);
   }
   if(btnIndex == 2)
   {
      m_e2eEnabled = enabled;
      m_btnE2E.Color(enabled ? clrBlack : clrGray);
   }
}

//+------------------------------------------------------------------+
bool CConnectorDialog::IsSimulateChecked(void)
{
   return m_chkSimulate.Checked();
}

//+------------------------------------------------------------------+
void CConnectorDialog::SetSimulateChecked(bool checked)
{
   m_chkSimulate.Checked(checked);
}

//+------------------------------------------------------------------+

