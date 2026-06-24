using NinjaTrader.Cbi;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// NinjaTrader-specific implementation of IInstrumentProvider.
    /// </summary>
    public sealed class NtInstrumentProvider : IInstrumentProvider
    {
        public BrokerInstrument GetInstrument(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            var instrument = Instrument.GetInstrument(name);
            if (instrument == null) return null;

            double pointValue = instrument.MasterInstrument?.PointValue ?? 1.0;
            return new BrokerInstrument(instrument.FullName, instrument.MasterInstrument?.Name ?? instrument.FullName, pointValue);
        }
    }
}
