namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Replaces the global static E2ETestRunning flag with an injected abstraction.
    /// </summary>
    public interface ITradingMode
    {
        bool IsSimulation { get; }
    }
}
