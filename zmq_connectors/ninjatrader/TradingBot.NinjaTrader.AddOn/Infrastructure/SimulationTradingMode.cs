using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Production trading mode (real orders). Simulation mode is controlled by the AddOn.
    /// </summary>
    public sealed class SimulationTradingMode : ITradingMode
    {
        public bool IsSimulation { get; }

        public SimulationTradingMode(bool isSimulation)
        {
            IsSimulation = isSimulation;
        }
    }
}
