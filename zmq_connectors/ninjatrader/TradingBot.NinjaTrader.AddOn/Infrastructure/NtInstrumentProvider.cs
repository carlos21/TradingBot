using System;
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
            string masterName = instrument.MasterInstrument?.Name ?? instrument.FullName;

            // Sanity check for MNQ: a wrong point value is a known cause of catastrophic position sizing.
            // CME spec: MNQ = $2 per full point ($0.50 per 0.25 tick).
            if (string.Equals(masterName, "MNQ", StringComparison.OrdinalIgnoreCase))
            {
                if (Math.Abs(pointValue - 2.0) > 0.01)
                {
                    // Use a simple console / debug logger since the domain logger is not available here.
                    System.Diagnostics.Debug.WriteLine($"CRITICAL: MNQ point value is {pointValue} but expected ~2.0. Position sizing will be catastrophically wrong.");
                }
            }

            return new BrokerInstrument(instrument.FullName, masterName, pointValue);
        }
    }
}
