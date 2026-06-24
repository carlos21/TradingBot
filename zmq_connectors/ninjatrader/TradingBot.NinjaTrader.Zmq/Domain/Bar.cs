using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Domain value object for a price bar.
    /// </summary>
    public sealed class Bar
    {
        public DateTime Time { get; }
        public double Open { get; }
        public double High { get; }
        public double Low { get; }
        public double Close { get; }
        public long Volume { get; }

        public Bar(DateTime time, double open, double high, double low, double close, long volume)
        {
            Time = time;
            Open = open;
            High = high;
            Low = low;
            Close = close;
            Volume = volume;
        }
    }
}
