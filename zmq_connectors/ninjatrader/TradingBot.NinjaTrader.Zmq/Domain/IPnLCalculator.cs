namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Calculates realized PnL from entry/exit value objects.
    /// </summary>
    public interface IPnLCalculator
    {
        PnlResult Calculate(BrokerOrder entryOrder, BrokerOrder exitOrder);
    }
}
