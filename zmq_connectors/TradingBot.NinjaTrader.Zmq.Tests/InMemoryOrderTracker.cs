using System;
using System.Collections.Generic;
using System.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Tests
{
    public sealed class InMemoryOrderTracker : IOrderTracker
    {
        private readonly Dictionary<string, BrokerOrder> _entries = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _stops = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _targets = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, BrokerOrder> _closes = new Dictionary<string, BrokerOrder>();
        private readonly Dictionary<string, PendingEntryInfo> _pendingEntries = new Dictionary<string, PendingEntryInfo>();
        private readonly Dictionary<string, PendingModifyInfo> _pendingModifies = new Dictionary<string, PendingModifyInfo>();
        private readonly HashSet<string> _expectedCancellations = new HashSet<string>();
        private readonly HashSet<string> _closePending = new HashSet<string>();

        public bool IsRestored { get; set; }

        public void TrackEntry(string tradeId, BrokerOrder order)
        {
            _entries[tradeId] = order;
        }

        public void TrackStopLoss(string tradeId, BrokerOrder order)
        {
            _stops[tradeId] = order;
        }

        public void TrackTakeProfit(string tradeId, BrokerOrder order)
        {
            _targets[tradeId] = order;
        }

        public void TrackPendingEntry(string tradeId, PendingEntryInfo entry)
        {
            _pendingEntries[tradeId] = entry;
        }

        public void TrackCloseOrder(string tradeId, BrokerOrder order)
        {
            _closes[tradeId] = order;
        }

        public void TrackPendingModify(string key, PendingModifyInfo info)
        {
            _pendingModifies[key] = info;
        }

        public bool TryGetEntry(string tradeId, out BrokerOrder order)
        {
            return _entries.TryGetValue(tradeId, out order);
        }

        public bool TryGetStopLoss(string tradeId, out BrokerOrder order)
        {
            return _stops.TryGetValue(tradeId, out order);
        }

        public bool TryGetTakeProfit(string tradeId, out BrokerOrder order)
        {
            return _targets.TryGetValue(tradeId, out order);
        }

        public bool TryGetPendingEntry(string tradeId, out PendingEntryInfo entry)
        {
            return _pendingEntries.TryGetValue(tradeId, out entry);
        }

        public bool TryGetCloseOrder(string tradeId, out BrokerOrder order)
        {
            return _closes.TryGetValue(tradeId, out order);
        }

        public bool TryGetPendingModify(string key, out PendingModifyInfo info)
        {
            return _pendingModifies.TryGetValue(key, out info);
        }

        public void RemovePendingModify(string key)
        {
            _pendingModifies.Remove(key);
        }

        public int PurgeStaleModifies(TimeSpan maxAge, ILogger logger)
        {
            var stale = _pendingModifies
                .Where(kv => DateTime.UtcNow - kv.Value.CreatedAt > maxAge)
                .Select(kv => kv.Key)
                .ToList();
            foreach (var key in stale) _pendingModifies.Remove(key);
            return stale.Count;
        }

        public bool TryGetTradeIdForOrder(BrokerOrder order, out string tradeId)
        {
            tradeId = null;
            if (order == null) return false;
            foreach (var kv in _entries)
                if (kv.Value.Name == order.Name) { tradeId = kv.Key; return true; }
            foreach (var kv in _stops)
                if (kv.Value.Name == order.Name) { tradeId = kv.Key; return true; }
            foreach (var kv in _targets)
                if (kv.Value.Name == order.Name) { tradeId = kv.Key; return true; }
            foreach (var kv in _closes)
                if (kv.Value.Name == order.Name) { tradeId = kv.Key; return true; }
            return false;
        }

        public void RemoveTrade(string tradeId)
        {
            _entries.Remove(tradeId);
            _stops.Remove(tradeId);
            _targets.Remove(tradeId);
            _closes.Remove(tradeId);
            _pendingEntries.Remove(tradeId);
            _pendingModifies.Remove(tradeId + ":sl");
            _pendingModifies.Remove(tradeId + ":tp");
            _closePending.Remove(tradeId);
        }

        public void Clear()
        {
            _entries.Clear();
            _stops.Clear();
            _targets.Clear();
            _closes.Clear();
            _pendingEntries.Clear();
            _pendingModifies.Clear();
            _expectedCancellations.Clear();
            _closePending.Clear();
        }

        public IEnumerable<string> GetActiveTradeIds()
        {
            return _entries.Keys.Union(_stops.Keys).Union(_targets.Keys).Union(_closes.Keys).Distinct();
        }

        public void ExpectCancellation(string orderName)
        {
            _expectedCancellations.Add(orderName);
        }

        public bool IsExpectedCancellation(string orderName)
        {
            return _expectedCancellations.Contains(orderName);
        }

        public void RemoveExpectedCancellation(string orderName)
        {
            _expectedCancellations.Remove(orderName);
        }

        public void MarkClosePending(string tradeId)
        {
            _closePending.Add(tradeId);
        }

        public bool IsClosePending(string tradeId)
        {
            return _closePending.Contains(tradeId);
        }
    }
}
