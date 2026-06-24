using System.Collections.Generic;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Strategy pattern: Track order state using domain value objects.
    /// Abstracts order tracking for testability.
    /// </summary>
    public interface IOrderTracker
    {
        void TrackEntry(string tradeId, BrokerOrder order);
        void TrackStopLoss(string tradeId, BrokerOrder order);
        void TrackTakeProfit(string tradeId, BrokerOrder order);
        void TrackPendingEntry(string tradeId, PendingEntryInfo entry);
        void TrackCloseOrder(string tradeId, BrokerOrder order);

        void TrackPendingModify(string key, PendingModifyInfo info);
        bool TryGetPendingModify(string key, out PendingModifyInfo info);
        void RemovePendingModify(string key);
        int PurgeStaleModifies(System.TimeSpan maxAge, ILogger logger);

        bool TryGetEntry(string tradeId, out BrokerOrder order);
        bool TryGetStopLoss(string tradeId, out BrokerOrder order);
        bool TryGetTakeProfit(string tradeId, out BrokerOrder order);
        bool TryGetPendingEntry(string tradeId, out PendingEntryInfo entry);
        bool TryGetCloseOrder(string tradeId, out BrokerOrder order);

        bool TryGetTradeIdForOrder(BrokerOrder order, out string tradeId);

        void RemoveTrade(string tradeId);
        void Clear();

        IEnumerable<string> GetActiveTradeIds();

        void ExpectCancellation(string orderName);
        bool IsExpectedCancellation(string orderName);
        void RemoveExpectedCancellation(string orderName);

        void MarkClosePending(string tradeId);
        bool IsClosePending(string tradeId);

        bool IsRestored { get; }
    }
}
