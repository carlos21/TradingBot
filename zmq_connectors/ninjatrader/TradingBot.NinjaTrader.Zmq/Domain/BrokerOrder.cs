using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Domain value object representing a broker order.
    /// Mirrors the subset of NinjaTrader.Cbi.Order used by the connector.
    /// </summary>
    public sealed class BrokerOrder
    {
        public string Name { get; }
        public string AccountName { get; }
        public BrokerInstrument Instrument { get; }
        public OrderType OrderType { get; }
        public OrderSide OrderSide { get; }
        public OrderState OrderState { get; }
        public int Quantity { get; }
        public int Filled { get; }
        public double AverageFillPrice { get; }
        public double StopPrice { get; }
        public double LimitPrice { get; }
        public string OcoId { get; }
        public DateTime Time { get; }

        public BrokerOrder(
            string name,
            string accountName,
            BrokerInstrument instrument,
            OrderType orderType,
            OrderSide orderSide,
            OrderState orderState,
            int quantity,
            int filled,
            double averageFillPrice = 0,
            double stopPrice = 0,
            double limitPrice = 0,
            string ocoId = null,
            DateTime? time = null)
        {
            Name = name;
            AccountName = accountName;
            Instrument = instrument;
            OrderType = orderType;
            OrderSide = orderSide;
            OrderState = orderState;
            Quantity = quantity;
            Filled = filled;
            AverageFillPrice = averageFillPrice;
            StopPrice = stopPrice;
            LimitPrice = limitPrice;
            OcoId = ocoId;
            Time = time ?? DateTime.UtcNow;
        }

        public bool IsWorking =>
            OrderState == OrderState.Working ||
            OrderState == OrderState.Accepted ||
            OrderState == OrderState.Submitted ||
            OrderState == OrderState.PartFilled;

        public BrokerOrder WithState(OrderState state) =>
            new BrokerOrder(Name, AccountName, Instrument, OrderType, OrderSide, state, Quantity, Filled,
                AverageFillPrice, StopPrice, LimitPrice, OcoId, Time);

        public BrokerOrder WithFill(double averageFillPrice, int filled) =>
            new BrokerOrder(Name, AccountName, Instrument, OrderType, OrderSide, OrderState, Quantity, filled,
                averageFillPrice, StopPrice, LimitPrice, OcoId, Time);
    }

    public enum OrderType
    {
        Market,
        Limit,
        StopMarket,
        StopLimit
    }

    public enum OrderSide
    {
        Buy,
        Sell,
        SellShort,
        BuyToCover
    }

    public enum OrderState
    {
        Initialized,
        Submitted,
        Accepted,
        Working,
        PartFilled,
        Filled,
        Cancelled,
        Rejected
    }
}
