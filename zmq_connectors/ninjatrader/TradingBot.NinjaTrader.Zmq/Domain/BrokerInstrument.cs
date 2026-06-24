namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Domain value object representing a tradable instrument.
    /// </summary>
    public sealed class BrokerInstrument
    {
        public string Name { get; }
        public string MasterInstrumentName { get; }
        public double PointValue { get; }

        public BrokerInstrument(string name, string masterInstrumentName, double pointValue)
        {
            Name = name ?? throw new System.ArgumentNullException(nameof(name));
            MasterInstrumentName = masterInstrumentName ?? name;
            PointValue = pointValue;
        }
    }
}
