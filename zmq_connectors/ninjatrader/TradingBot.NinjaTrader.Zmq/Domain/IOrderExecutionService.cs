using System.Collections.Generic;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Port for order execution. Implementations translate to broker-specific APIs.
    /// </summary>
    public interface IOrderExecutionService
    {
        BrokerOrder CreateEntryOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, string tradeId);
        BrokerOrder CreateStopLossOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, double stopPrice, string tradeId);
        BrokerOrder CreateTakeProfitOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, double limitPrice, string tradeId);
        BrokerOrder CreateMarketCloseOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, string tradeId);

        void SubmitOrder(BrokerOrder order);
        void CancelOrder(BrokerOrder order);

        /// <summary>
        /// Amends an existing working order in place. Set a price/quantity parameter to
        /// null to leave that value unchanged. Implementations should use the broker's
        /// native amend API (e.g. NinjaTrader Account.Change) so OCO linkage is preserved.
        /// </summary>
        void ModifyOrder(BrokerOrder order, double? stopPrice, double? limitPrice, int? quantity = null);

        /// <summary>
        /// Submit multiple orders to the broker in a single call.
        /// Required for OCO-linked bracket orders (stop + target).
        /// </summary>
        void SubmitOrders(IReadOnlyList<BrokerOrder> orders);

        BrokerOrder FindOrderByName(BrokerAccount account, string orderName);
        IReadOnlyList<BrokerOrder> GetWorkingOrders(BrokerAccount account);
        IReadOnlyList<BrokerOrder> GetAllOrders(BrokerAccount account);

        /// <summary>
        /// Returns the non-zero positions currently held in the account.
        /// </summary>
        IReadOnlyList<BrokerPosition> GetAccountPositions(BrokerAccount account);
    }
}
