namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Domain value object representing an actual account position held at the broker.
    /// </summary>
    public sealed class BrokerPosition
    {
        public string AccountName { get; }
        public BrokerInstrument Instrument { get; }
        public int Quantity { get; }
        public string Direction { get; }
        public double AveragePrice { get; }

        public BrokerPosition(string accountName, BrokerInstrument instrument, int quantity, string direction, double averagePrice)
        {
            AccountName = accountName ?? throw new System.ArgumentNullException(nameof(accountName));
            Instrument = instrument ?? throw new System.ArgumentNullException(nameof(instrument));
            Quantity = quantity;
            Direction = direction ?? throw new System.ArgumentNullException(nameof(direction));
            AveragePrice = averagePrice;
        }

        public bool IsLong => Direction == "long";
        public bool IsShort => Direction == "short";
    }
}
