//+------------------------------------------------------------------+
//|                              Infrastructure/OrderTracking.mqh    |
//|  IOrderTracker implementation using dynamic arrays.              |
//|  O(n) lookups — acceptable since active trades are typically <50 |
//|  Pattern: Strategy (order tracking strategy)                     |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"

//+------------------------------------------------------------------+
//| Internal struct for entry tracking                               |
//+------------------------------------------------------------------+
struct EntryRecord
{
   string tradeId;
   ulong  ticket;
   double slPoints;
   double rrRatio;
};

//+------------------------------------------------------------------+
//| Internal struct for simple ticket mapping                        |
//+------------------------------------------------------------------+
struct TicketMapping
{
   string tradeId;
   ulong  ticket;
};

//+------------------------------------------------------------------+
//| Internal struct for pending modify                               |
//+------------------------------------------------------------------+
struct ModifyRecord
{
   string tradeId;
   double newSl;
   double newTp;
};

//+------------------------------------------------------------------+
//| OrderStateManager — tracks order lifecycle in memory             |
//+------------------------------------------------------------------+
class OrderStateManager : public IOrderTracker
{
private:
   EntryRecord    m_entries[];
   TicketMapping  m_stopLosses[];
   TicketMapping  m_takeProfits[];
   TicketMapping  m_closeOrders[];
   ModifyRecord   m_pendingModifies[];

public:
   OrderStateManager() {}
   ~OrderStateManager() { Clear(); }

   //--- IOrderTracker implementation

   void TrackEntry(string tradeId, ulong ticket, double slPoints, double rrRatio) override
   {
      int idx = FindEntryIndex(tradeId);
      if(idx >= 0)
      {
         m_entries[idx].ticket = ticket;
         m_entries[idx].slPoints = slPoints;
         m_entries[idx].rrRatio = rrRatio;
      }
      else
      {
         int size = ArraySize(m_entries);
         ArrayResize(m_entries, size + 1);
         m_entries[size].tradeId = tradeId;
         m_entries[size].ticket = ticket;
         m_entries[size].slPoints = slPoints;
         m_entries[size].rrRatio = rrRatio;
      }
   }

   void TrackStopLoss(string tradeId, ulong ticket) override
   {
      int idx = FindInArray(m_stopLosses, tradeId);
      if(idx >= 0)
         m_stopLosses[idx].ticket = ticket;
      else
      {
         int size = ArraySize(m_stopLosses);
         ArrayResize(m_stopLosses, size + 1);
         m_stopLosses[size].tradeId = tradeId;
         m_stopLosses[size].ticket = ticket;
      }
   }

   void TrackTakeProfit(string tradeId, ulong ticket) override
   {
      int idx = FindInArray(m_takeProfits, tradeId);
      if(idx >= 0)
         m_takeProfits[idx].ticket = ticket;
      else
      {
         int size = ArraySize(m_takeProfits);
         ArrayResize(m_takeProfits, size + 1);
         m_takeProfits[size].tradeId = tradeId;
         m_takeProfits[size].ticket = ticket;
      }
   }

   void TrackCloseOrder(string tradeId, ulong ticket) override
   {
      int idx = FindInArray(m_closeOrders, tradeId);
      if(idx >= 0)
         m_closeOrders[idx].ticket = ticket;
      else
      {
         int size = ArraySize(m_closeOrders);
         ArrayResize(m_closeOrders, size + 1);
         m_closeOrders[size].tradeId = tradeId;
         m_closeOrders[size].ticket = ticket;
      }
   }

   bool TryGetEntry(string tradeId, ulong &ticket, double &slPoints, double &rrRatio) override
   {
      int idx = FindEntryIndex(tradeId);
      if(idx < 0) return false;
      ticket = m_entries[idx].ticket;
      slPoints = m_entries[idx].slPoints;
      rrRatio = m_entries[idx].rrRatio;
      return true;
   }

   bool TryGetStopLoss(string tradeId, ulong &ticket) override
   {
      return TryGetFromArray(m_stopLosses, tradeId, ticket);
   }

   bool TryGetTakeProfit(string tradeId, ulong &ticket) override
   {
      return TryGetFromArray(m_takeProfits, tradeId, ticket);
   }

   bool TryGetCloseOrder(string tradeId, ulong &ticket) override
   {
      return TryGetFromArray(m_closeOrders, tradeId, ticket);
   }

   bool TryGetTradeIdForTicket(ulong ticket, string &tradeId) override
   {
      // Search all arrays for this ticket
      for(int i = 0; i < ArraySize(m_entries); i++)
         if(m_entries[i].ticket == ticket) { tradeId = m_entries[i].tradeId; return true; }
      for(int i = 0; i < ArraySize(m_stopLosses); i++)
         if(m_stopLosses[i].ticket == ticket) { tradeId = m_stopLosses[i].tradeId; return true; }
      for(int i = 0; i < ArraySize(m_takeProfits); i++)
         if(m_takeProfits[i].ticket == ticket) { tradeId = m_takeProfits[i].tradeId; return true; }
      for(int i = 0; i < ArraySize(m_closeOrders); i++)
         if(m_closeOrders[i].ticket == ticket) { tradeId = m_closeOrders[i].tradeId; return true; }
      return false;
   }

   void RemoveTrade(string tradeId) override
   {
      // Remove from m_entries
      int idx = FindEntryIndex(tradeId);
      if(idx >= 0)
      {
         int size = ArraySize(m_entries);
         for(int j = idx; j < size - 1; j++)
            m_entries[j] = m_entries[j + 1];
         ArrayResize(m_entries, size - 1);
      }

      // Remove from m_stopLosses
      idx = FindInArray(m_stopLosses, tradeId);
      if(idx >= 0)
      {
         int size = ArraySize(m_stopLosses);
         for(int j = idx; j < size - 1; j++)
            m_stopLosses[j] = m_stopLosses[j + 1];
         ArrayResize(m_stopLosses, size - 1);
      }

      // Remove from m_takeProfits
      idx = FindInArray(m_takeProfits, tradeId);
      if(idx >= 0)
      {
         int size = ArraySize(m_takeProfits);
         for(int j = idx; j < size - 1; j++)
            m_takeProfits[j] = m_takeProfits[j + 1];
         ArrayResize(m_takeProfits, size - 1);
      }

      // Remove from m_closeOrders
      idx = FindInArray(m_closeOrders, tradeId);
      if(idx >= 0)
      {
         int size = ArraySize(m_closeOrders);
         for(int j = idx; j < size - 1; j++)
            m_closeOrders[j] = m_closeOrders[j + 1];
         ArrayResize(m_closeOrders, size - 1);
      }

      RemovePendingModify(tradeId);
   }

   void SetPendingModify(string tradeId, double newSl, double newTp) override
   {
      int idx = FindModifyIndex(tradeId);
      if(idx >= 0)
      {
         m_pendingModifies[idx].newSl = newSl;
         m_pendingModifies[idx].newTp = newTp;
      }
      else
      {
         int size = ArraySize(m_pendingModifies);
         ArrayResize(m_pendingModifies, size + 1);
         m_pendingModifies[size].tradeId = tradeId;
         m_pendingModifies[size].newSl = newSl;
         m_pendingModifies[size].newTp = newTp;
      }
   }

   bool TryGetPendingModify(string tradeId, double &newSl, double &newTp) override
   {
      int idx = FindModifyIndex(tradeId);
      if(idx < 0) return false;
      newSl = m_pendingModifies[idx].newSl;
      newTp = m_pendingModifies[idx].newTp;
      return true;
   }

   void RemovePendingModify(string tradeId) override
   {
      int idx = FindModifyIndex(tradeId);
      if(idx < 0) return;
      int size = ArraySize(m_pendingModifies);
      for(int j = idx; j < size - 1; j++)
         m_pendingModifies[j] = m_pendingModifies[j + 1];
      ArrayResize(m_pendingModifies, size - 1);
   }

   void Clear() override
   {
      ArrayResize(m_entries, 0);
      ArrayResize(m_stopLosses, 0);
      ArrayResize(m_takeProfits, 0);
      ArrayResize(m_closeOrders, 0);
      ArrayResize(m_pendingModifies, 0);
   }

   int GetActiveCount() override
   {
      return ArraySize(m_entries);
   }

   void GetActiveTradeIds(string &tradeIds[]) override
   {
      int count = ArraySize(m_entries);
      ArrayResize(tradeIds, count);
      for(int i = 0; i < count; i++)
         tradeIds[i] = m_entries[i].tradeId;
   }

private:
   //--- Helpers for array operations (no & syntax for old MQL5 builds)

   int FindEntryIndex(string tradeId)
   {
      for(int i = 0; i < ArraySize(m_entries); i++)
         if(m_entries[i].tradeId == tradeId) return i;
      return -1;
   }

   int FindModifyIndex(string tradeId)
   {
      for(int i = 0; i < ArraySize(m_pendingModifies); i++)
         if(m_pendingModifies[i].tradeId == tradeId) return i;
      return -1;
   }

   int FindInArray(TicketMapping &arr[], string tradeId)
   {
      for(int i = 0; i < ArraySize(arr); i++)
         if(arr[i].tradeId == tradeId) return i;
      return -1;
   }

   bool TryGetFromArray(TicketMapping &arr[], string tradeId, ulong &ticket)
   {
      int idx = FindInArray(arr, tradeId);
      if(idx < 0) return false;
      ticket = arr[idx].ticket;
      return true;
   }


};
