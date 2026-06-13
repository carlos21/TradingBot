// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Order Tracking
// Concrete implementation of IOrderTracker
// ═══════════════════════════════════════════════════════════════════════

using System.Collections.Generic;
using System.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Thread-safe order state manager using locking.
    /// Implements IOrderTracker for dependency injection and testability.
    /// </summary>
    internal sealed class OrderStateManager : IOrderTracker
    {
        private readonly Dictionary<string, Order> _entryOrders = new Dictionary<string, Order>();
        private readonly Dictionary<string, Order> _stopLossOrders = new Dictionary<string, Order>();
        private readonly Dictionary<string, Order> _takeProfitOrders = new Dictionary<string, Order>();
        private readonly Dictionary<string, Order> _closeOrders = new Dictionary<string, Order>();  // Closing orders
        private readonly Dictionary<string, PendingEntryInfo> _pendingEntries = new Dictionary<string, PendingEntryInfo>();
        private readonly Dictionary<string, PendingModifyInfo> _pendingModifies = new Dictionary<string, PendingModifyInfo>();

        private readonly HashSet<string> _expectedCancellations = new HashSet<string>();  // order names we expect to be cancelled
        private readonly object _lock = new object();
        
        public bool IsRestored { get; private set; } = false;

        public void TrackEntry(string tradeId, Order order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _entryOrders[tradeId] = order;
        }

        public void TrackStopLoss(string tradeId, Order order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _stopLossOrders[tradeId] = order;
        }

        public void TrackTakeProfit(string tradeId, Order order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _takeProfitOrders[tradeId] = order;
        }

        public void TrackPendingEntry(string tradeId, PendingEntryInfo entry)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (entry == null) throw new System.ArgumentNullException(nameof(entry));
            lock (_lock) _pendingEntries[tradeId] = entry;
        }

        public void TrackPendingModify(string tradeId, PendingModifyInfo info)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (info == null) throw new System.ArgumentNullException(nameof(info));
            lock (_lock) _pendingModifies[tradeId] = info;
        }

        public bool TryGetPendingModify(string tradeId, out PendingModifyInfo info)
        {
            lock (_lock) return _pendingModifies.TryGetValue(tradeId, out info);
        }

        public void RemovePendingModify(string tradeId)
        {
            lock (_lock) _pendingModifies.Remove(tradeId);
        }

        public bool TryGetEntry(string tradeId, out Order order)
        {
            lock (_lock) return _entryOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetStopLoss(string tradeId, out Order order)
        {
            lock (_lock) return _stopLossOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetTakeProfit(string tradeId, out Order order)
        {
            lock (_lock) return _takeProfitOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetPendingEntry(string tradeId, out PendingEntryInfo entry)
        {
            lock (_lock) return _pendingEntries.TryGetValue(tradeId, out entry);
        }

        public bool TryGetTradeIdForOrder(Order order, out string tradeId)
        {
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock)
            {
                // First: Try to extract trade_id from order name (e.g., "Entry_trade-123")
                if (!string.IsNullOrEmpty(order.Name))
                {
                    if (order.Name.StartsWith("Entry_"))
                    {
                        tradeId = order.Name.Substring(6);
                        if (_entryOrders.ContainsKey(tradeId)) return true;
                    }
                    else if (order.Name.StartsWith("Stop_"))
                    {
                        tradeId = order.Name.Substring(5);
                        if (_stopLossOrders.ContainsKey(tradeId)) return true;
                    }
                    else if (order.Name.StartsWith("Target_"))
                    {
                        tradeId = order.Name.Substring(7);
                        if (_takeProfitOrders.ContainsKey(tradeId)) return true;
                    }
                    else if (order.Name.StartsWith("Close_"))
                    {
                        tradeId = order.Name.Substring(6);
                        if (_closeOrders.ContainsKey(tradeId)) return true;
                    }
                }
                
                // Fallback: Check by object reference
                foreach (var kvp in _entryOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _stopLossOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _takeProfitOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _closeOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                tradeId = null;
                return false;
            }
        }

        public void TrackCloseOrder(string tradeId, Order order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _closeOrders[tradeId] = order;
        }

        public bool TryGetCloseOrder(string tradeId, out Order order)
        {
            lock (_lock) return _closeOrders.TryGetValue(tradeId, out order);
        }

        public void RemoveTrade(string tradeId)
        {
            lock (_lock)
            {
                _entryOrders.Remove(tradeId);
                _stopLossOrders.Remove(tradeId);
                _takeProfitOrders.Remove(tradeId);
                _closeOrders.Remove(tradeId);
                _pendingEntries.Remove(tradeId);
                // Clean up both keyed modify slots (tradeId:sl and tradeId:tp)
                _pendingModifies.Remove(tradeId);
                _pendingModifies.Remove(tradeId + ":sl");
                _pendingModifies.Remove(tradeId + ":tp");
            }
        }

        public void Clear()
        {
            lock (_lock)
            {
                _entryOrders.Clear();
                _stopLossOrders.Clear();
                _takeProfitOrders.Clear();
                _closeOrders.Clear();
                _pendingEntries.Clear();
                _pendingModifies.Clear();
                _expectedCancellations.Clear();
            }
        }

        public IEnumerable<string> GetActiveTradeIds()
        {
            lock (_lock)
            {
                var ids = new HashSet<string>();
                foreach (var id in _pendingEntries.Keys) ids.Add(id);
                foreach (var id in _entryOrders.Keys) ids.Add(id);
                return ids;
            }
        }

        public void ExpectCancellation(string orderName)
        {
            if (orderName == null) throw new System.ArgumentNullException(nameof(orderName));
            lock (_lock) _expectedCancellations.Add(orderName);
        }

        public bool IsExpectedCancellation(string orderName)
        {
            if (orderName == null) return false;
            lock (_lock) return _expectedCancellations.Contains(orderName);
        }

        public void RemoveExpectedCancellation(string orderName)
        {
            if (orderName == null) return;
            lock (_lock) _expectedCancellations.Remove(orderName);
        }

        /// <summary>
        /// Recovery: Scan broker orders and rebuild tracking dictionaries.
        /// Called after reconnect to recover from crash.
        /// </summary>
        public void RestoreFromBrokerOrders(Account account, ILogger logger)
        {
            if (account == null)
            {
                logger?.Warning("[Recovery] No account available for restore");
                return;
            }

            lock (_lock)
            {
                // First pass: identify trades that still have at least one active order
                var activeTradeIds = new HashSet<string>();
                // Snapshot to avoid collection-modified-during-enumeration
                var ordersSnapshot = account.Orders.ToArray();
                foreach (var order in ordersSnapshot)
                {
                    string tradeId = ExtractTradeIdFromOrderName(order.Name);
                    if (string.IsNullOrEmpty(tradeId)) continue;

                    bool isActive = order.OrderState == OrderState.Working
                                 || order.OrderState == OrderState.Accepted
                                 || order.OrderState == OrderState.PartFilled
                                 || order.OrderState == OrderState.Submitted;

                    if (isActive) activeTradeIds.Add(tradeId);
                }

                int entryCount = 0;
                int stopCount = 0;
                int targetCount = 0;
                int closeCount = 0;

                // Snapshot to avoid collection-modified-during-enumeration
                var orders = account.Orders.ToArray();
                foreach (var order in orders)
                {
                    // Try to extract trade_id from order name (e.g., "Entry_trade-123" -> "trade-123")
                    string tradeId = ExtractTradeIdFromOrderName(order.Name);
                    if (string.IsNullOrEmpty(tradeId)) continue;

                    // Only restore orders that are still live on the broker.
                    bool isLive = order.OrderState == OrderState.Working
                               || order.OrderState == OrderState.Accepted
                               || order.OrderState == OrderState.Submitted
                               || order.OrderState == OrderState.PartFilled;

                    if (!isLive)
                    {
                        // Filled entries may represent open positions, but only keep them
                        // if we can see an active child order (stop/target/close) for the same trade.
                        if (order.OrderState == OrderState.Filled && activeTradeIds.Contains(tradeId))
                        {
                            // allow below
                        }
                        else
                        {
                            logger?.Debug($"[Recovery] Skipping {order.Name} state={order.OrderState}");
                            continue;
                        }
                    }

                    // Track based on order type
                    if (IsEntryOrder(order))
                    {
                        _entryOrders[tradeId] = order;
                        entryCount++;
                        logger?.Info($"[Recovery] Restored entry order for {tradeId}");
                    }
                    else if (IsStopOrder(order))
                    {
                        _stopLossOrders[tradeId] = order;
                        stopCount++;
                        logger?.Info($"[Recovery] Restored stop order for {tradeId}");
                    }
                    else if (IsTargetOrder(order))
                    {
                        _takeProfitOrders[tradeId] = order;
                        targetCount++;
                        logger?.Info($"[Recovery] Restored target order for {tradeId}");
                    }
                    else if (IsCloseOrder(order))
                    {
                        _closeOrders[tradeId] = order;
                        closeCount++;
                        logger?.Info($"[Recovery] Restored close order for {tradeId}");
                    }
                }

                IsRestored = true;
                int totalRestored = entryCount + stopCount + targetCount + closeCount;
                if (totalRestored > 0)
                {
                    logger?.Success($"[Recovery] Restored {entryCount} entries, {stopCount} stops, {targetCount} targets, {closeCount} close orders from broker ({account.Name})");
                }
            }
        }

        private static string ExtractTradeIdFromOrderName(string orderName)
        {
            if (string.IsNullOrEmpty(orderName)) return null;
            
            // Format: "Entry_trade-123", "Stop_trade-123", "Target_trade-123"
            if (orderName.StartsWith("Entry_"))
                return orderName.Substring(6);
            if (orderName.StartsWith("Stop_"))
                return orderName.Substring(5);
            if (orderName.StartsWith("Target_"))
                return orderName.Substring(7);
            if (orderName.StartsWith("Close_"))
                return orderName.Substring(6);
            
            return null;
        }

        private static bool IsEntryOrder(Order order) => 
            order?.Name != null && order.Name.StartsWith("Entry_");
        
        private static bool IsStopOrder(Order order) =>
            order?.Name != null && order.Name.StartsWith("Stop_");
        
        private static bool IsTargetOrder(Order order) =>
            order?.Name != null && order.Name.StartsWith("Target_");
        
        private static bool IsCloseOrder(Order order) =>
            order?.Name != null && order.Name.StartsWith("Close_");
    }
}
