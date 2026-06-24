using System.Collections.Generic;
using System.Linq;
using NinjaTrader.Cbi;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// NinjaTrader-specific implementation of IAccountProvider.
    /// </summary>
    public sealed class NtAccountProvider : IAccountProvider
    {
        public IReadOnlyList<BrokerAccount> GetAccounts()
        {
            var list = new List<BrokerAccount>();
            foreach (Account account in Account.All)
            {
                double cashValue = 0;
                try
                {
                    cashValue = account.Get(AccountItem.CashValue, Currency.UsDollar);
                }
                catch { /* best effort */ }
                list.Add(new BrokerAccount(account.Name, account.Connection != null, cashValue));
            }
            return list;
        }

        public BrokerAccount GetAccount(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            foreach (Account account in Account.All)
            {
                if (account.Name == name)
                {
                    double cashValue = 0;
                    try
                    {
                        cashValue = account.Get(AccountItem.CashValue, Currency.UsDollar);
                    }
                    catch { /* best effort */ }
                    return new BrokerAccount(account.Name, account.Connection != null, cashValue);
                }
            }
            return null;
        }
    }
}
