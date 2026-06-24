using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// NinjaTrader-specific implementation of IBarHistoryService.
    /// </summary>
    public sealed class NtBarHistoryService : IBarHistoryService
    {
        private readonly IInstrumentProvider _instrumentProvider;

        public NtBarHistoryService(IInstrumentProvider instrumentProvider)
        {
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
        }

        public async Task<IReadOnlyList<Bar>> RequestHistoryAsync(BrokerInstrument instrument, DateTime startUtc, DateTime endUtc, int minutes = 1)
        {
            var ntInstrument = Instrument.GetInstrument(instrument.Name);
            if (ntInstrument == null)
                throw new InvalidOperationException($"Instrument '{instrument.Name}' not found");

            var tcs = new TaskCompletionSource<IReadOnlyList<Bar>>();
            var barsRequest = new BarsRequest(ntInstrument, startUtc, endUtc)
            {
                BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = minutes },
                TradingHours = TradingHours.Get("Default 24 x 7")
            };

            try
            {
                barsRequest.Request((bars, errorCode, errorMessage) =>
                {
                    try
                    {
                        if (errorCode != ErrorCode.NoError)
                        {
                            tcs.TrySetException(new InvalidOperationException($"BarsRequest failed: {errorMessage}"));
                            return;
                        }

                        if (bars?.Bars == null)
                        {
                            tcs.TrySetException(new InvalidOperationException("BarsRequest returned null bars"));
                            return;
                        }

                        var result = new List<Bar>();
                        for (int i = 0; i < bars.Bars.Count; i++)
                        {
                            result.Add(new Bar(
                                bars.Bars.GetTime(i),
                                bars.Bars.GetOpen(i),
                                bars.Bars.GetHigh(i),
                                bars.Bars.GetLow(i),
                                bars.Bars.GetClose(i),
                                (long)bars.Bars.GetVolume(i)));
                        }
                        tcs.TrySetResult(result);
                    }
                    catch (Exception ex)
                    {
                        tcs.TrySetException(ex);
                    }
                });

                var timeoutTask = Task.Delay(TimeSpan.FromSeconds(30));
                var completedTask = await Task.WhenAny(tcs.Task, timeoutTask);
                if (completedTask == timeoutTask)
                    throw new TimeoutException("BarsRequest timed out after 30 seconds");

                return await tcs.Task;
            }
            finally
            {
                barsRequest?.Dispose();
            }
        }
    }
}
