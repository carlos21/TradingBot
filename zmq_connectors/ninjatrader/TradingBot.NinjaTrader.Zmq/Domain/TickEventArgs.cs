using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Event args for tick data.
    /// </summary>
    public sealed class TickEventArgs : EventArgs
    {
        public string Instrument { get; }
        public double Price { get; }
        public long Volume { get; }
        public DateTime Time { get; }
        public double? Bid { get; }
        public double? Ask { get; }

        public TickEventArgs(string instrument, double price, long volume, DateTime time,
            double? bid = null, double? ask = null)
        {
            Instrument = instrument ?? throw new ArgumentNullException(nameof(instrument));
            Price = price;
            Volume = volume;
            Time = time;
            Bid = bid;
            Ask = ask;
        }
    }
}
