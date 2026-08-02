using Nt = global::NinjaTrader.Cbi;
using Domain = TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Maps between NinjaTrader.Cbi.Order and domain BrokerOrder value objects.
    /// </summary>
    public static class BrokerOrderMapper
    {
        /// <param name="commission">
        /// Commission for the order. NinjaTrader.Cbi.Order does not expose commission;
        /// it is reported per NinjaTrader.Cbi.Execution, so callers handling an
        /// execution event pass Execution.Commission here.
        /// </param>
        public static Domain.BrokerOrder ToBrokerOrder(Nt.Order order, double commission = 0.0)
        {
            if (order == null) return null;

            var instrument = new Domain.BrokerInstrument(
                order.Instrument?.FullName,
                order.Instrument?.MasterInstrument?.Name ?? order.Instrument?.FullName,
                order.Instrument?.MasterInstrument?.PointValue ?? 1.0);

            return new Domain.BrokerOrder(
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
                commission,
                null,
                order.Time);
        }

        public static Domain.OrderSide ToOrderSide(Nt.OrderAction action)
        {
            if (action == Nt.OrderAction.Buy) return Domain.OrderSide.Buy;
            if (action == Nt.OrderAction.Sell) return Domain.OrderSide.Sell;
            if (action == Nt.OrderAction.SellShort) return Domain.OrderSide.SellShort;
            return Domain.OrderSide.BuyToCover;
        }

        public static Nt.OrderAction ToNtOrderAction(Domain.OrderSide side)
        {
            switch (side)
            {
                case Domain.OrderSide.Buy: return Nt.OrderAction.Buy;
                case Domain.OrderSide.Sell: return Nt.OrderAction.Sell;
                case Domain.OrderSide.SellShort: return Nt.OrderAction.SellShort;
                case Domain.OrderSide.BuyToCover: return Nt.OrderAction.BuyToCover;
                default: throw new System.ArgumentOutOfRangeException(nameof(side));
            }
        }

        public static Domain.OrderType ToOrderType(Nt.OrderType type)
        {
            if (type == Nt.OrderType.Market) return Domain.OrderType.Market;
            if (type == Nt.OrderType.Limit) return Domain.OrderType.Limit;
            if (type == Nt.OrderType.StopMarket) return Domain.OrderType.StopMarket;
            return Domain.OrderType.StopLimit;
        }

        public static Nt.OrderType ToNtOrderType(Domain.OrderType type)
        {
            switch (type)
            {
                case Domain.OrderType.Market: return Nt.OrderType.Market;
                case Domain.OrderType.Limit: return Nt.OrderType.Limit;
                case Domain.OrderType.StopMarket: return Nt.OrderType.StopMarket;
                case Domain.OrderType.StopLimit: return Nt.OrderType.StopLimit;
                default: throw new System.ArgumentOutOfRangeException(nameof(type));
            }
        }

        public static Domain.OrderState ToOrderState(Nt.OrderState state)
        {
            if (state == Nt.OrderState.Initialized) return Domain.OrderState.Initialized;
            if (state == Nt.OrderState.Submitted) return Domain.OrderState.Submitted;
            if (state == Nt.OrderState.Accepted) return Domain.OrderState.Accepted;
            if (state == Nt.OrderState.Working) return Domain.OrderState.Working;
            if (state == Nt.OrderState.PartFilled) return Domain.OrderState.PartFilled;
            if (state == Nt.OrderState.Filled) return Domain.OrderState.Filled;
            if (state == Nt.OrderState.Cancelled) return Domain.OrderState.Cancelled;
            if (state == Nt.OrderState.ChangePending) return Domain.OrderState.ChangePending;
            if (state == Nt.OrderState.ChangeSubmitted) return Domain.OrderState.ChangeSubmitted;
            return Domain.OrderState.Rejected;
        }
    }
}
