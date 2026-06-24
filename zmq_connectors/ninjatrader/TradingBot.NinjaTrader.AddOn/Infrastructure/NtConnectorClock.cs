using System;
using System.Threading;
using System.Threading.Tasks;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// System clock implementation for production use.
    /// </summary>
    public sealed class NtConnectorClock : IConnectorClock
    {
        public DateTime UtcNow => DateTime.UtcNow;
        public DateTime Now => DateTime.Now;

        public void Sleep(int milliseconds) => Thread.Sleep(milliseconds);

        public Task Delay(int milliseconds, CancellationToken cancellationToken = default) =>
            Task.Delay(milliseconds, cancellationToken);
    }
}
