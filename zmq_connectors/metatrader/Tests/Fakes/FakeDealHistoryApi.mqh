//+------------------------------------------------------------------+
//|                             Tests/Fakes/FakeDealHistoryApi.mqh   |
//|  Fake IDealHistoryApi — scripted deal list + order SL/TP lookup. |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeDealHistoryApi — scripted deal history                       |
//+------------------------------------------------------------------+
class FakeDealHistoryApi : public IDealHistoryApi
{
private:
   ulong  m_tickets[];
   long   m_magics[];
   long   m_entries[];
   long   m_reasons[];
   double m_prices[];
   string m_comments[];
   long   m_orderTickets[];
   long   m_times[];
   double m_volumes[];
   double m_profits[];
   double m_commissions[];

   ulong  m_orderSlTickets[];
   double m_orderSls[];
   double m_orderTps[];

   bool   m_selectResult;

   int _FindDeal(ulong ticket)
   {
      for(int i = 0; i < ArraySize(m_tickets); i++)
         if(m_tickets[i] == ticket)
            return i;
      return -1;
   }

   int _FindOrder(ulong orderTicket)
   {
      for(int i = 0; i < ArraySize(m_orderSlTickets); i++)
         if(m_orderSlTickets[i] == orderTicket)
            return i;
      return -1;
   }

public:
   FakeDealHistoryApi()
   {
      m_selectResult = true;
   }

   //--- Test hooks -------------------------------------------------
   void AddDeal(ulong ticket, long magic, long entry, long reason,
                double price, string comment, long orderTicket,
                long time, double volume, double profit, double commission)
   {
      int n = ArraySize(m_tickets);
      ArrayResize(m_tickets, n + 1);      m_tickets[n] = ticket;
      ArrayResize(m_magics, n + 1);       m_magics[n] = magic;
      ArrayResize(m_entries, n + 1);      m_entries[n] = entry;
      ArrayResize(m_reasons, n + 1);      m_reasons[n] = reason;
      ArrayResize(m_prices, n + 1);       m_prices[n] = price;
      ArrayResize(m_comments, n + 1);     m_comments[n] = comment;
      ArrayResize(m_orderTickets, n + 1); m_orderTickets[n] = orderTicket;
      ArrayResize(m_times, n + 1);        m_times[n] = time;
      ArrayResize(m_volumes, n + 1);      m_volumes[n] = volume;
      ArrayResize(m_profits, n + 1);      m_profits[n] = profit;
      ArrayResize(m_commissions, n + 1);  m_commissions[n] = commission;
   }

   void SetOrderSlTp(ulong orderTicket, double sl, double tp)
   {
      int n = ArraySize(m_orderSlTickets);
      ArrayResize(m_orderSlTickets, n + 1); m_orderSlTickets[n] = orderTicket;
      ArrayResize(m_orderSls, n + 1);       m_orderSls[n] = sl;
      ArrayResize(m_orderTps, n + 1);       m_orderTps[n] = tp;
   }

   void SetSelectResult(bool result) { m_selectResult = result; }

   //--- IDealHistoryApi --------------------------------------------
   bool Select(datetime from, datetime to) override { return m_selectResult; }
   int  Total() override { return ArraySize(m_tickets); }

   ulong TicketByIndex(int index) override
   {
      if(index < 0 || index >= ArraySize(m_tickets))
         return 0;
      return m_tickets[index];
   }

   long   Magic(ulong ticket) override      { int i = _FindDeal(ticket); return i >= 0 ? m_magics[i] : 0; }
   long   Entry(ulong ticket) override      { int i = _FindDeal(ticket); return i >= 0 ? m_entries[i] : 0; }
   long   Reason(ulong ticket) override     { int i = _FindDeal(ticket); return i >= 0 ? m_reasons[i] : 0; }
   double Price(ulong ticket) override      { int i = _FindDeal(ticket); return i >= 0 ? m_prices[i] : 0.0; }
   string Comment(ulong ticket) override    { int i = _FindDeal(ticket); return i >= 0 ? m_comments[i] : ""; }
   long   OrderTicket(ulong ticket) override{ int i = _FindDeal(ticket); return i >= 0 ? m_orderTickets[i] : 0; }
   long   Time(ulong ticket) override       { int i = _FindDeal(ticket); return i >= 0 ? m_times[i] : 0; }
   double Volume(ulong ticket) override     { int i = _FindDeal(ticket); return i >= 0 ? m_volumes[i] : 0.0; }
   double Profit(ulong ticket) override     { int i = _FindDeal(ticket); return i >= 0 ? m_profits[i] : 0.0; }
   double Commission(ulong ticket) override { int i = _FindDeal(ticket); return i >= 0 ? m_commissions[i] : 0.0; }

   bool SelectOrder(ulong orderTicket) override
   {
      return _FindOrder(orderTicket) >= 0;
   }

   double OrderStopLoss(ulong orderTicket) override
   {
      int i = _FindOrder(orderTicket);
      return i >= 0 ? m_orderSls[i] : 0.0;
   }

   double OrderTakeProfit(ulong orderTicket) override
   {
      int i = _FindOrder(orderTicket);
      return i >= 0 ? m_orderTps[i] : 0.0;
   }
};
