using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Value object: Pending modify information for cancel+replace pattern.
    /// </summary>
    public sealed class PendingModifyInfo
    {
        public double NewPrice { get; }
        public BrokerInstrument Instrument { get; }
        public OrderSide OrderSide { get; }
        public int Quantity { get; }
        public bool IsTarget { get; }
        public DateTime CreatedAt { get; }

        public PendingModifyInfo(double newPrice, BrokerInstrument instrument, OrderSide orderSide, int quantity, bool isTarget = false)
        {
            NewPrice = newPrice;
            Instrument = instrument ?? throw new ArgumentNullException(nameof(instrument));
            OrderSide = orderSide;
            Quantity = quantity;
            IsTarget = isTarget;
            CreatedAt = DateTime.UtcNow;
        }
    }
}
