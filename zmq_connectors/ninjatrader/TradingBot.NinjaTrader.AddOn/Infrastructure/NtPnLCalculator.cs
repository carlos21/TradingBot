using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Calculates realized PnL from domain order value objects.
    /// </summary>
    public sealed class NtPnLCalculator : IPnLCalculator
    {
        public PnlResult Calculate(BrokerOrder entryOrder, BrokerOrder exitOrder)
        {
            if (entryOrder == null || exitOrder == null)
                return new PnlResult(0, 0);

            int quantity = exitOrder.Filled > 0 ? exitOrder.Filled : exitOrder.Quantity;
            if (quantity <= 0)
                return new PnlResult(0, 0);

            double entryPrice = entryOrder.AverageFillPrice;
            double exitPrice = exitOrder.AverageFillPrice;
            if (entryPrice == 0 || exitPrice == 0)
                return new PnlResult(0, 0);

            double pointValue = exitOrder.Instrument?.PointValue ?? 2.0;
            bool isLong = entryOrder.OrderSide == OrderSide.Buy;
            double priceDiff = isLong ? (exitPrice - entryPrice) : (entryPrice - exitPrice);
            double grossPnl = priceDiff * quantity * pointValue;

            return new PnlResult(grossPnl, 0);
        }
    }
}
