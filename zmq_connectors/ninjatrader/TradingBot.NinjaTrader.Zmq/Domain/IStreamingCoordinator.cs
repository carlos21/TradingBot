namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Orchestrates live market-data and live-bar streaming for a given instrument.
    /// </summary>
    public interface IStreamingCoordinator
    {
        string CurrentInstrument { get; }
        bool IsStreaming { get; }
        (long ticks, long bars, long partials) GetStats();
        bool Start(string instrument);
        void Stop();
    }
}
