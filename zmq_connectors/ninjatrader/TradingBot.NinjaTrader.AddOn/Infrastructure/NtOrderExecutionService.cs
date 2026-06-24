using System;
using System.Collections.Generic;
using System.Linq;
using NinjaTrader.Cbi;
using TradingBot.NinjaTrader.Zmq.Domain;
using OrderSide = TradingBot.NinjaTrader.Zmq.Domain.OrderSide;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class NtOrderExecutionService : IOrderExecutionService
    {
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IAccountProvider _accountProvider;

        public NtOrderExecutionService(IInstrumentProvider instrumentProvider, IAccountProvider accountProvider)
        {
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
        }

        public BrokerOrder CreateEntryOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), NinjaTrader.Cbi.OrderType.Market,
                OrderEntry.Automated, TimeInForce.Gtc, quantity, 0, 0, null, $"Entry_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public BrokerOrder CreateStopLossOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, double stopPrice, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), NinjaTrader.Cbi.OrderType.StopMarket,
                OrderEntry.Automated, TimeInForce.Gtc, quantity, 0, stopPrice, $"OCO_{tradeId}", $"Stop_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public BrokerOrder CreateTakeProfitOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, double limitPrice, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), NinjaTrader.Cbi.OrderType.Limit,
                OrderEntry.Automated, TimeInForce.Gtc, quantity, limitPrice, 0, $"OCO_{tradeId}", $"Target_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public BrokerOrder CreateMarketCloseOrder(BrokerInstrument instrument, BrokerAccount account, OrderSide side, int quantity, string tradeId)
        {
            var ntInstrument = GetNtInstrument(instrument);
            var ntAccount = GetNtAccount(account);
            if (ntInstrument == null || ntAccount == null) return null;

            var order = ntAccount.CreateOrder(
                ntInstrument, BrokerOrderMapper.ToNtOrderAction(side), NinjaTrader.Cbi.OrderType.Market,
                OrderEntry.Automated, TimeInForce.Gtc, quantity, 0, 0, null, $"Close_{tradeId}", DateTime.MinValue, null);

            return BrokerOrderMapper.ToBrokerOrder(order);
        }

        public void SubmitOrder(BrokerOrder order)
        {
            var ntAccount = GetNtAccount(_accountProvider.GetAccount(order.AccountName));
            var ntOrder = FindNtOrderByName(ntAccount, order.Name);
            if (ntOrder != null)
                ntAccount.Submit(new[] { ntOrder });
        }

        public void CancelOrder(BrokerOrder order)
        {
            var ntAccount = GetNtAccount(_accountProvider.GetAccount(order.AccountName));
            var ntOrder = FindNtOrderByName(ntAccount, order.Name);
            if (ntOrder != null)
                ntAccount.Cancel(new[] { ntOrder });
        }

        public BrokerOrder FindOrderByName(BrokerAccount account, string orderName)
        {
            var ntAccount = GetNtAccount(account);
            return BrokerOrderMapper.ToBrokerOrder(FindNtOrderByName(ntAccount, orderName));
        }

        public IReadOnlyList<BrokerOrder> GetWorkingOrders(BrokerAccount account)
        {
            var result = new List<BrokerOrder>();
            var ntAccount = GetNtAccount(account);
            if (ntAccount == null) return result;

            foreach (var order in ntAccount.Orders ?? System.Linq.Enumerable.Empty<NinjaTrader.Cbi.Order>())
            {
                if (IsActive(order))
                    result.Add(BrokerOrderMapper.ToBrokerOrder(order));
            }
            return result;
        }

        public IReadOnlyList<BrokerOrder> GetAllOrders(BrokerAccount account)
        {
            var result = new List<BrokerOrder>();
            var ntAccount = GetNtAccount(account);
            if (ntAccount == null) return result;

            foreach (var order in ntAccount.Orders ?? System.Linq.Enumerable.Empty<NinjaTrader.Cbi.Order>())
                result.Add(BrokerOrderMapper.ToBrokerOrder(order));
            return result;
        }

        private static NinjaTrader.Cbi.Order FindNtOrderByName(Account account, string orderName)
        {
            if (account == null || string.IsNullOrEmpty(orderName)) return null;
            var orders = account.Orders.ToArray();
            NinjaTrader.Cbi.Order fallback = null;
            for (int i = orders.Length - 1; i >= 0; i--)
            {
                var order = orders[i];
                if (order.Name != orderName) continue;
                if (IsActive(order)) return order;
                if (fallback == null) fallback = order;
            }
            return fallback;
        }

        private static bool IsActive(NinjaTrader.Cbi.Order order)
        {
            return order.OrderState == NinjaTrader.Cbi.OrderState.Working ||
                   order.OrderState == NinjaTrader.Cbi.OrderState.Accepted ||
                   order.OrderState == NinjaTrader.Cbi.OrderState.Submitted ||
                   order.OrderState == NinjaTrader.Cbi.OrderState.PartFilled ||
                   order.OrderState == NinjaTrader.Cbi.OrderState.Filled;
        }

        private Instrument GetNtInstrument(BrokerInstrument instrument)
        {
            if (instrument == null) return null;
            return Instrument.GetInstrument(instrument.Name);
        }

        private Account GetNtAccount(BrokerAccount account)
        {
            if (account == null) return null;
            foreach (Account a in Account.All)
                if (a.Name == account.Name) return a;
            return null;
        }
    }
}
