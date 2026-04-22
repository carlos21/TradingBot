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
      TrackInArray(m_stopLosses, tradeId, ticket);
   }

   void TrackTakeProfit(string tradeId, ulong ticket) override
   {
      TrackInArray(m_takeProfits, tradeId, ticket);
   }

   void TrackCloseOrder(string tradeId, ulong ticket) override
   {
      TrackInArray(m_closeOrders, tradeId, ticket);
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
      RemoveFromArray(m_entries, tradeId);
      RemoveFromArray(m_stopLosses, tradeId);
      RemoveFromArray(m_takeProfits, tradeId);
      RemoveFromArray(m_closeOrders, tradeId);
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
   //--- Helpers for array operations

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

   void TrackInArray(TicketMapping &arr[], string tradeId, ulong ticket)
   {
      int idx = FindInArray(arr, tradeId);
      if(idx >= 0)
         arr[idx].ticket = ticket;
      else
      {
         int size = ArraySize(arr);
         ArrayResize(arr, size + 1);
         arr[size].tradeId = tradeId;
         arr[size].ticket = ticket;
      }
   }

   bool TryGetFromArray(TicketMapping &arr[], string tradeId, ulong &ticket)
   {
      int idx = FindInArray(arr, tradeId);
      if(idx < 0) return false;
      ticket = arr[idx].ticket;
      return true;
   }

   void RemoveFromArray(TicketMapping &arr[], string tradeId)
   {
      int idx = FindInArray(arr, tradeId);
      if(idx < 0) return;
      int size = ArraySize(arr);
      for(int j = idx; j < size - 1; j++)
         arr[j] = arr[j + 1];
      ArrayResize(arr, size - 1);
   }

   void RemoveFromArray(EntryRecord &arr[], string tradeId)
   {
      int idx = FindEntryIndex(tradeId);
      if(idx < 0) return;
      int size = ArraySize(arr);
      for(int j = idx; j < size - 1; j++)
         arr[j] = arr[j + 1];
      ArrayResize(arr, size - 1);
   }
};
