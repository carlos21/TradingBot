using System;
using System.Threading;
using System.Threading.Tasks;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Abstracts time so tests can be deterministic.
    /// </summary>
    public interface IConnectorClock
    {
        DateTime UtcNow { get; }
        DateTime Now { get; }
        void Sleep(int milliseconds);
        Task Delay(int milliseconds, CancellationToken cancellationToken = default);
    }
}
