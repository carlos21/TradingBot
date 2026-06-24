using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Port for historical bar requests. Implementations use broker-specific APIs.
    /// </summary>
    public interface IBarHistoryService
    {
        Task<IReadOnlyList<Bar>> RequestHistoryAsync(BrokerInstrument instrument, DateTime startUtc, DateTime endUtc, int minutes = 1);
    }
}
