using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Value object: Pending entry information.
    /// </summary>
    public sealed class PendingEntryInfo
    {
        public string Direction { get; }
        public double SlPoints { get; }
        public double RrRatio { get; }

        public PendingEntryInfo(string direction, double slPoints, double rrRatio)
        {
            Direction = direction ?? throw new ArgumentNullException(nameof(direction));
            SlPoints = slPoints;
            RrRatio = rrRatio;
        }
    }
}
