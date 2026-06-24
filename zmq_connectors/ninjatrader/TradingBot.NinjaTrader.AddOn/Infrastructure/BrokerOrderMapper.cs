using NinjaTrader.Cbi;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Maps between NinjaTrader.Cbi.Order and domain BrokerOrder value objects.
    /// </summary>
    public static class BrokerOrderMapper
    {
        public static BrokerOrder ToBrokerOrder(NinjaTrader.Cbi.Order order)
        {
            if (order == null) return null;

            var instrument = new BrokerInstrument(
                order.Instrument?.FullName,
                order.Instrument?.MasterInstrument?.Name ?? order.Instrument?.FullName,
                order.Instrument?.MasterInstrument?.PointValue ?? 1.0);

            return new BrokerOrder(
                order.Name,
                order.Account?.Name,
                instrument,
                ToOrderType(order.OrderType),
                ToOrderSide(order.OrderAction),
                ToOrderState(order.OrderState),
                order.Quantity,
                order.Filled,
                order.AverageFillPrice,
                order.StopPrice,
                order.LimitPrice,
                null,
                order.Time);
        }

        public static OrderSide ToOrderSide(NinjaTrader.Cbi.OrderAction action)
        {
            if (action == NinjaTrader.Cbi.OrderAction.Buy) return OrderSide.Buy;
            if (action == NinjaTrader.Cbi.OrderAction.Sell) return OrderSide.Sell;
            if (action == NinjaTrader.Cbi.OrderAction.SellShort) return OrderSide.SellShort;
            return OrderSide.BuyToCover;
        }

        public static NinjaTrader.Cbi.OrderAction ToNtOrderAction(OrderSide side)
        {
            switch (side)
            {
                case OrderSide.Buy: return NinjaTrader.Cbi.OrderAction.Buy;
                case OrderSide.Sell: return NinjaTrader.Cbi.OrderAction.Sell;
                case OrderSide.SellShort: return NinjaTrader.Cbi.OrderAction.SellShort;
                case OrderSide.BuyToCover: return NinjaTrader.Cbi.OrderAction.BuyToCover;
                default: throw new System.ArgumentOutOfRangeException(nameof(side));
            }
        }

        public static OrderType ToOrderType(NinjaTrader.Cbi.OrderType type)
        {
            if (type == NinjaTrader.Cbi.OrderType.Market) return OrderType.Market;
            if (type == NinjaTrader.Cbi.OrderType.Limit) return OrderType.Limit;
            if (type == NinjaTrader.Cbi.OrderType.StopMarket) return OrderType.StopMarket;
            return OrderType.StopLimit;
        }

        public static NinjaTrader.Cbi.OrderType ToNtOrderType(OrderType type)
        {
            switch (type)
            {
                case OrderType.Market: return NinjaTrader.Cbi.OrderType.Market;
                case OrderType.Limit: return NinjaTrader.Cbi.OrderType.Limit;
                case OrderType.StopMarket: return NinjaTrader.Cbi.OrderType.StopMarket;
                case OrderType.StopLimit: return NinjaTrader.Cbi.OrderType.StopLimit;
                default: throw new System.ArgumentOutOfRangeException(nameof(type));
            }
        }

        public static OrderState ToOrderState(NinjaTrader.Cbi.OrderState state)
        {
            if (state == NinjaTrader.Cbi.OrderState.Initialized) return OrderState.Initialized;
            if (state == NinjaTrader.Cbi.OrderState.Submitted) return OrderState.Submitted;
            if (state == NinjaTrader.Cbi.OrderState.Accepted) return OrderState.Accepted;
            if (state == NinjaTrader.Cbi.OrderState.Working) return OrderState.Working;
            if (state == NinjaTrader.Cbi.OrderState.PartFilled) return OrderState.PartFilled;
            if (state == NinjaTrader.Cbi.OrderState.Filled) return OrderState.Filled;
            if (state == NinjaTrader.Cbi.OrderState.Cancelled) return OrderState.Cancelled;
            return OrderState.Rejected;
        }
    }
}
