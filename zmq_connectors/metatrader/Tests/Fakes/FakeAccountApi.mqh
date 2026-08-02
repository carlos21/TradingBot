//+------------------------------------------------------------------+
//|                                Tests/Fakes/FakeAccountApi.mqh    |
//|  Fake IAccountApi — programmable login/balance/demo flag.        |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeAccountApi — scripted account state                          |
//+------------------------------------------------------------------+
class FakeAccountApi : public IAccountApi
{
private:
   long   m_login;
   double m_balance;
   bool   m_isDemo;

public:
   FakeAccountApi()
   {
      m_login = 12345678;
      m_balance = 100000.0;
      m_isDemo = true;
   }

   void SetLogin(long login)       { m_login = login; }
   void SetBalance(double balance) { m_balance = balance; }
   void SetIsDemo(bool isDemo)     { m_isDemo = isDemo; }

   long   Login() override   { return m_login; }
   double Balance() override { return m_balance; }
   bool   IsDemo() override  { return m_isDemo; }
};
