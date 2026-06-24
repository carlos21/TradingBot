namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Resolves trading instruments without depending on NinjaTrader.Cbi.
    /// </summary>
    public interface IInstrumentProvider
    {
        BrokerInstrument GetInstrument(string name);
    }
}
