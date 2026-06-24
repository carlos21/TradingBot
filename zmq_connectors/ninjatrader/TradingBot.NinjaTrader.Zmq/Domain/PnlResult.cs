namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Value object for PnL calculation results.
    /// </summary>
    public sealed class PnlResult
    {
        public double RealizedPnl { get; }
        public double Commission { get; }

        public PnlResult(double realizedPnl, double commission)
        {
            RealizedPnl = realizedPnl;
            Commission = commission;
        }
    }
}
