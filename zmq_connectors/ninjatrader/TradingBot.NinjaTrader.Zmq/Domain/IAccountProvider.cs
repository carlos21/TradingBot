using System.Collections.Generic;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Provides access to broker accounts without depending on NinjaTrader.Cbi.
    /// </summary>
    public interface IAccountProvider
    {
        IReadOnlyList<BrokerAccount> GetAccounts();
        BrokerAccount GetAccount(string name);
    }
}
