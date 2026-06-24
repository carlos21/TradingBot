namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Domain value object representing a broker account.
    /// </summary>
    public sealed class BrokerAccount
    {
        public string Name { get; }
        public bool HasConnection { get; }
        public double CashValue { get; }

        public BrokerAccount(string name, bool hasConnection = true, double cashValue = 0)
        {
            Name = name ?? throw new System.ArgumentNullException(nameof(name));
            HasConnection = hasConnection;
            CashValue = cashValue;
        }
    }
}
