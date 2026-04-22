// ═══════════════════════════════════════════════════════════════════════
// Domain Layer: Contracts (Interfaces)
// Abstract contracts that define the boundaries between layers
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Strategy pattern: Serialize/deserialize messages.
    /// Allows swapping JSON for other formats (MessagePack, Protobuf) without
    /// changing business logic.
    /// </summary>
    public interface IMessageSerializer
    {
        string Serialize(MessageEnvelope envelope);
        MessageEnvelope Deserialize(string json);
        T DeserializePayload<T>(JObject payload, string key);
    }

    /// <summary>
    /// Observer pattern: Handle incoming commands.
    /// Each command type has its own handler for Single Responsibility.
    /// </summary>
    public interface ICommandHandler
    {
        string CommandType { get; }
        void Handle(JObject payload);
    }

    /// <summary>
    /// Strategy pattern: Track order state.
    /// Abstracts order tracking for testability (can mock for unit tests).
    /// </summary>
    public interface IOrderTracker
    {
        void TrackEntry(string tradeId, Order order);
        void TrackStopLoss(string tradeId, Order order);
        void TrackTakeProfit(string tradeId, Order order);
        void TrackPendingEntry(string tradeId, PendingEntryInfo entry);
        void TrackAtmStrategy(string tradeId, string atmStrategyName);
        void TrackCloseOrder(string tradeId, Order order);
        void TrackPendingModify(string tradeId, PendingModifyInfo info);
        bool TryGetPendingModify(string tradeId, out PendingModifyInfo info);
        void RemovePendingModify(string tradeId);

        bool TryGetEntry(string tradeId, out Order order);
        bool TryGetStopLoss(string tradeId, out Order order);
        bool TryGetTakeProfit(string tradeId, out Order order);
        bool TryGetPendingEntry(string tradeId, out PendingEntryInfo entry);
        bool TryGetTradeIdForOrder(Order order, out string tradeId);
        bool TryGetAtmStrategy(string tradeId, out string atmStrategyName);
        bool TryGetCloseOrder(string tradeId, out Order order);

        void RemoveTrade(string tradeId);
        void Clear();

        IEnumerable<string> GetActiveTradeIds();
        
        // Expected cancellation tracking (for modify/close workflows)
        void ExpectCancellation(string orderName);
        bool IsExpectedCancellation(string orderName);
        void RemoveExpectedCancellation(string orderName);
        
        // Recovery methods
        void RestoreFromBrokerOrders(Account account, ILogger logger);
        bool IsRestored { get; }
    }

    /// <summary>
    /// Facade pattern: Abstract market data operations.
    /// Decouples business logic from NinjaTrader-specific market data APIs.
    /// </summary>
    public interface IMarketDataService : IDisposable
    {
        event EventHandler<TickEventArgs> TickReceived;
        void Subscribe(string instrument);
        void Unsubscribe();
    }

    /// <summary>
    /// Strategy pattern: Logging abstraction for testability.
    /// </summary>
    public interface ILogger
    {
        void Info(string message);
        void Warning(string message);
        void Error(string message, Exception ex = null);
        void Success(string message);
        void Debug(string message);
    }
}
