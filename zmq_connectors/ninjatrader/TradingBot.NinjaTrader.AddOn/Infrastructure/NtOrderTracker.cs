using System.Collections.Generic;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// In-memory order state manager implementing IOrderTracker.
    /// This class has no direct NinjaTrader dependencies.
    /// </summary>
    public sealed class NtOrderTracker : IOrderTracker
    {
        private readonly Dictionary<string, BrokerOrder> _entryOrders = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _stopLossOrders = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _takeProfitOrders = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _closeOrders = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, PendingEntryInfo> _pendingEntries = new Dictionary<string, PendingEntryInfo>();
        private readonly Dictionary<string, PendingModifyInfo> _pendingModifies = new Dictionary<string, PendingModifyInfo>();
        private readonly HashSet<string> _expectedCancellations = new HashSet<string>();
        private readonly HashSet<string> _closePendingTrades = new HashSet<string>();
        private readonly object _lock = new object();

        public bool IsRestored { get; private set; }

        public void TrackEntry(string tradeId, BrokerOrder order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _entryOrders[tradeId] = order;
        }

        public void TrackStopLoss(string tradeId, BrokerOrder order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _stopLossOrders[tradeId] = order;
        }

        public void TrackTakeProfit(string tradeId, BrokerOrder order)
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

        public void TrackCloseOrder(string tradeId, BrokerOrder order)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock) _closeOrders[tradeId] = order;
        }

        public void TrackPendingModify(string key, PendingModifyInfo info)
        {
            if (key == null) throw new System.ArgumentNullException(nameof(key));
            if (info == null) throw new System.ArgumentNullException(nameof(info));
            lock (_lock) _pendingModifies[key] = info;
        }

        public bool TryGetPendingModify(string key, out PendingModifyInfo info)
        {
            lock (_lock) return _pendingModifies.TryGetValue(key, out info);
        }

        public void RemovePendingModify(string key)
        {
            lock (_lock) _pendingModifies.Remove(key);
        }

        public int PurgeStaleModifies(System.TimeSpan maxAge, ILogger logger)
        {
            var now = System.DateTime.UtcNow;
            var staleKeys = new List<string>();
            lock (_lock)
            {
                foreach (var kvp in _pendingModifies)
                {
                    if (now - kvp.Value.CreatedAt > maxAge)
                        staleKeys.Add(kvp.Key);
                }
                foreach (var key in staleKeys)
                    _pendingModifies.Remove(key);
            }
            foreach (var key in staleKeys)
                logger?.Warning($"[OrderTracker] Purged stale pending modify slot '{key}'");
            return staleKeys.Count;
        }

        public bool TryGetEntry(string tradeId, out BrokerOrder order)
        {
            lock (_lock) return _entryOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetStopLoss(string tradeId, out BrokerOrder order)
        {
            lock (_lock) return _stopLossOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetTakeProfit(string tradeId, out BrokerOrder order)
        {
            lock (_lock) return _takeProfitOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetPendingEntry(string tradeId, out PendingEntryInfo entry)
        {
            lock (_lock) return _pendingEntries.TryGetValue(tradeId, out entry);
        }

        public bool TryGetCloseOrder(string tradeId, out BrokerOrder order)
        {
            lock (_lock) return _closeOrders.TryGetValue(tradeId, out order);
        }

        public bool TryGetTradeIdForOrder(BrokerOrder order, out string tradeId)
        {
            if (order == null) throw new System.ArgumentNullException(nameof(order));
            lock (_lock)
            {
                tradeId = null;
                if (!string.IsNullOrEmpty(order.Name))
                {
                    if (order.Name.StartsWith("Entry_") && _entryOrders.ContainsKey(order.Name.Substring(6)))
                    { tradeId = order.Name.Substring(6); return true; }
                    if (order.Name.StartsWith("Stop_") && _stopLossOrders.ContainsKey(order.Name.Substring(5)))
                    { tradeId = order.Name.Substring(5); return true; }
                    if (order.Name.StartsWith("Target_") && _takeProfitOrders.ContainsKey(order.Name.Substring(7)))
                    { tradeId = order.Name.Substring(7); return true; }
                    if (order.Name.StartsWith("Close_") && _closeOrders.ContainsKey(order.Name.Substring(6)))
                    { tradeId = order.Name.Substring(6); return true; }
                }
                foreach (var kvp in _entryOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _stopLossOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _takeProfitOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                foreach (var kvp in _closeOrders)
                    if (ReferenceEquals(kvp.Value, order)) { tradeId = kvp.Key; return true; }
                return false;
            }
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
                _closePendingTrades.Remove(tradeId);
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
                _closePendingTrades.Clear();
                IsRestored = false;
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

        public void MarkClosePending(string tradeId)
        {
            if (tradeId == null) throw new System.ArgumentNullException(nameof(tradeId));
            lock (_lock) _closePendingTrades.Add(tradeId);
        }

        public bool IsClosePending(string tradeId)
        {
            if (tradeId == null) return false;
            lock (_lock) return _closePendingTrades.Contains(tradeId);
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
    }
}
