using System;
using System.Collections.Generic;
using System.Linq;
using Nt = global::NinjaTrader.Cbi;
using Domain = TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class NtOrderExecutionService : Domain.IOrderExecutionService
    {
        private readonly Domain.IInstrumentProvider _instrumentProvider;
        private readonly Domain.IAccountProvider _accountProvider;

        public NtOrderExecutionService(Domain.IInstrumentProvider instrumentProvider, Domain.IAccountProvider accountProvider)
        {
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
        }

        public Domain.BrokerOrder CreateEntryOrder(Domain.BrokerInstrument instrument, Domain.BrokerAccount account, Domain.OrderSide side, int quantity, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), Nt.OrderType.Market,
                Nt.OrderEntry.Automated, Nt.TimeInForce.Gtc, quantity, 0, 0, null, $"Entry_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public Domain.BrokerOrder CreateStopLossOrder(Domain.BrokerInstrument instrument, Domain.BrokerAccount account, Domain.OrderSide side, int quantity, double stopPrice, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), Nt.OrderType.StopMarket,
                Nt.OrderEntry.Automated, Nt.TimeInForce.Gtc, quantity, 0, stopPrice, $"OCO_{tradeId}", $"Stop_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public Domain.BrokerOrder CreateTakeProfitOrder(Domain.BrokerInstrument instrument, Domain.BrokerAccount account, Domain.OrderSide side, int quantity, double limitPrice, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), Nt.OrderType.Limit,
                Nt.OrderEntry.Automated, Nt.TimeInForce.Gtc, quantity, limitPrice, 0, $"OCO_{tradeId}", $"Target_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public Domain.BrokerOrder CreateMarketCloseOrder(Domain.BrokerInstrument instrument, Domain.BrokerAccount account, Domain.OrderSide side, int quantity, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), Nt.OrderType.Market,
                Nt.OrderEntry.Automated, Nt.TimeInForce.Gtc, quantity, 0, 0, null, $"Close_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public void SubmitOrder(Domain.BrokerOrder order)
        {
            var ntAccount = GetNtAccount(_accountProvider.GetAccount(order.AccountName));
            var ntOrder = FindNtOrderByName(ntAccount, order.Name);
            if (ntOrder != null && IsSubmittable(ntOrder))
                ntAccount.Submit(new[] { ntOrder });
        }

        public void SubmitOrders(IReadOnlyList<Domain.BrokerOrder> orders)
        {
            if (orders == null || orders.Count == 0) return;

            // All orders in a bracket belong to the same account.
            var ntAccount = GetNtAccount(_accountProvider.GetAccount(orders[0].AccountName));
            if (ntAccount == null) return;

            var ntOrders = new List<Nt.Order>(orders.Count);
            foreach (var order in orders)
            {
                var ntOrder = FindNtOrderByName(ntAccount, order.Name);
                if (ntOrder != null && IsSubmittable(ntOrder))
                    ntOrders.Add(ntOrder);
            }

            if (ntOrders.Count > 0)
                ntAccount.Submit(ntOrders.ToArray());
        }

        public void CancelOrder(Domain.BrokerOrder order)
        {
            var ntAccount = GetNtAccount(_accountProvider.GetAccount(order.AccountName));
            var ntOrder = FindNtOrderByName(ntAccount, order.Name);
            if (ntOrder != null)
                ntAccount.Cancel(new[] { ntOrder });
        }

        public Domain.BrokerOrder FindOrderByName(Domain.BrokerAccount account, string orderName)
        {
            var ntAccount = GetNtAccount(account);
            return BrokerOrderMapper.ToBrokerOrder(FindNtOrderByName(ntAccount, orderName));
        }

        public IReadOnlyList<Domain.BrokerOrder> GetWorkingOrders(Domain.BrokerAccount account)
        {
            var result = new List<Domain.BrokerOrder>();
            var ntAccount = GetNtAccount(account);
            if (ntAccount == null) return result;

            foreach (var order in ntAccount.Orders ?? Enumerable.Empty<Nt.Order>())
            {
                if (IsActive(order))
                    result.Add(BrokerOrderMapper.ToBrokerOrder(order));
            }
            return result;
        }

        public IReadOnlyList<Domain.BrokerOrder> GetAllOrders(Domain.BrokerAccount account)
        {
            var result = new List<Domain.BrokerOrder>();
            var ntAccount = GetNtAccount(account);
            if (ntAccount == null) return result;

            foreach (var order in ntAccount.Orders ?? Enumerable.Empty<Nt.Order>())
                result.Add(BrokerOrderMapper.ToBrokerOrder(order));
            return result;
        }

        private static Nt.Order FindNtOrderByName(Nt.Account account, string orderName)
        {
            if (account == null || string.IsNullOrEmpty(orderName)) return null;
            var orders = account.Orders.ToArray();
            Nt.Order fallback = null;
            for (int i = orders.Length - 1; i >= 0; i--)
            {
                var order = orders[i];
                if (order.Name != orderName) continue;
                if (IsActive(order)) return order;
                if (fallback == null) fallback = order;
            }
            return fallback;
        }

        private static bool IsActive(Nt.Order order)
        {
            return order.OrderState == Nt.OrderState.Working ||
                   order.OrderState == Nt.OrderState.Accepted ||
                   order.OrderState == Nt.OrderState.Submitted ||
                   order.OrderState == Nt.OrderState.PartFilled ||
                   order.OrderState == Nt.OrderState.Filled;
        }

        private static bool IsSubmittable(Nt.Order order)
        {
            // Do not try to submit an order that is already terminal; NinjaTrader
            // will throw a dialog error.
            return order.OrderState == Nt.OrderState.Initialized ||
                   order.OrderState == Nt.OrderState.Working ||
                   order.OrderState == Nt.OrderState.Accepted ||
                   order.OrderState == Nt.OrderState.Submitted ||
                   order.OrderState == Nt.OrderState.PartFilled;
        }

        private Nt.Instrument GetNtInstrument(Domain.BrokerInstrument instrument)
        {
            if (instrument == null) return null;
            return Nt.Instrument.GetInstrument(instrument.Name);
        }

        private Nt.Account GetNtAccount(Domain.BrokerAccount account)
        {
            if (account == null) return null;
            foreach (Nt.Account a in Nt.Account.All)
                if (a.Name == account.Name) return a;
            return null;
        }
    }
}
