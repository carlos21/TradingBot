using System.Collections.Generic;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Orchestrates live market-data and live-bar streaming for a given instrument.
    /// </summary>
    public interface IStreamingCoordinator
    {
        string CurrentInstrument { get; }
        bool IsStreaming { get; }

        /// <summary>
        /// Snapshot of all currently subscribed instruments (full names), in
        /// subscription order. Used by connection recovery to resubscribe after
        /// a restart.
        /// </summary>
        IReadOnlyList<string> SubscribedInstruments { get; }

        (long ticks, long bars, long partials) GetStats();
        bool Start(string instrument);

        /// <summary>
        /// Stops streaming a single instrument. Other instruments keep streaming.
        /// Returns false when the instrument was not subscribed.
        /// </summary>
        bool Stop(string instrument);

        /// <summary>Stops streaming all instruments.</summary>
        void Stop();
    }
}
