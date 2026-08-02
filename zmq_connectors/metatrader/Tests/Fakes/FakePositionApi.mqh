//+------------------------------------------------------------------+
//|                               Tests/Fakes/FakePositionApi.mqh    |
//|  Fake IPositionApi — scripted list of open positions.            |
//|  TicketByIndex/SelectByTicket select; getters read the selected. |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakePositionApi — scripted open positions                        |
//+------------------------------------------------------------------+
class FakePositionApi : public IPositionApi
{
private:
   ulong  m_tickets[];
   long   m_magics[];
   string m_comments[];
   long   m_types[];
   double m_priceOpens[];
   double m_stopLosses[];
   double m_takeProfits[];
   double m_volumes[];
   int    m_selected;   //--- index of currently selected position, -1 none

public:
   FakePositionApi()
   {
      m_selected = -1;
   }

   //--- Test hook: append a position -------------------------------
   void AddPosition(ulong ticket, long magic, string comment, long type,
                    double priceOpen, double sl, double tp, double volume)
   {
      int n = ArraySize(m_tickets);
      ArrayResize(m_tickets, n + 1);     m_tickets[n] = ticket;
      ArrayResize(m_magics, n + 1);      m_magics[n] = magic;
      ArrayResize(m_comments, n + 1);    m_comments[n] = comment;
      ArrayResize(m_types, n + 1);       m_types[n] = type;
      ArrayResize(m_priceOpens, n + 1);  m_priceOpens[n] = priceOpen;
      ArrayResize(m_stopLosses, n + 1);  m_stopLosses[n] = sl;
      ArrayResize(m_takeProfits, n + 1); m_takeProfits[n] = tp;
      ArrayResize(m_volumes, n + 1);     m_volumes[n] = volume;
   }

   void Clear()
   {
      ArrayResize(m_tickets, 0);
      ArrayResize(m_magics, 0);
      ArrayResize(m_comments, 0);
      ArrayResize(m_types, 0);
      ArrayResize(m_priceOpens, 0);
      ArrayResize(m_stopLosses, 0);
      ArrayResize(m_takeProfits, 0);
      ArrayResize(m_volumes, 0);
      m_selected = -1;
   }

   //--- IPositionApi -----------------------------------------------
   int Total() override { return ArraySize(m_tickets); }

   ulong TicketByIndex(int index) override
   {
      if(index < 0 || index >= ArraySize(m_tickets))
      {
         m_selected = -1;
         return 0;
      }
      m_selected = index;
      return m_tickets[index];
   }

   bool SelectByTicket(ulong ticket) override
   {
      for(int i = 0; i < ArraySize(m_tickets); i++)
         if(m_tickets[i] == ticket)
         {
            m_selected = i;
            return true;
         }
      m_selected = -1;
      return false;
   }

   long   Magic() override      { return m_selected >= 0 ? m_magics[m_selected] : 0; }
   string Comment() override    { return m_selected >= 0 ? m_comments[m_selected] : ""; }
   long   Type() override       { return m_selected >= 0 ? m_types[m_selected] : 0; }
   double PriceOpen() override  { return m_selected >= 0 ? m_priceOpens[m_selected] : 0.0; }
   double StopLoss() override   { return m_selected >= 0 ? m_stopLosses[m_selected] : 0.0; }
   double TakeProfit() override { return m_selected >= 0 ? m_takeProfits[m_selected] : 0.0; }
   double Volume() override     { return m_selected >= 0 ? m_volumes[m_selected] : 0.0; }
};
