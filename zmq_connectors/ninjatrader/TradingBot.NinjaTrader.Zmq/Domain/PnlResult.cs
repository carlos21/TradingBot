namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Value object for PnL calculation results.
    /// </summary>
    /// <remarks>
    /// <see cref="RealizedPnl"/> represents the gross realized PnL before commissions.
    /// The net realized PnL is <see cref="RealizedPnl"/> - <see cref="Commission"/>.
    /// </remarks>
    public sealed class PnlResult
    {
        /// <summary>
        /// Gross realized PnL before commissions.
        /// </summary>
        public double RealizedPnl { get; }

        /// <summary>
        /// Total commission/fees for the round-trip trade.
        /// </summary>
        public double Commission { get; }

        public PnlResult(double realizedPnl, double commission)
        {
            RealizedPnl = realizedPnl;
            Commission = commission;
        }
    }
}
