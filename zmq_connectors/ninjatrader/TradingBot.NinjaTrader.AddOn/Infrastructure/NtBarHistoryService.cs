using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using NinjaTrader.Core;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using Domain = TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// NinjaTrader-specific implementation of IBarHistoryService.
    /// </summary>
    public sealed class NtBarHistoryService : Domain.IBarHistoryService
    {
        private readonly Domain.IInstrumentProvider _instrumentProvider;
        private readonly Domain.ILogger _logger;
        private static readonly TimeZoneInfo PlatformTimeZone = Globals.GeneralOptions.TimeZoneInfo;

        public NtBarHistoryService(Domain.IInstrumentProvider instrumentProvider, Domain.ILogger logger)
        {
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        }

        public async Task<IReadOnlyList<Domain.Bar>> RequestHistoryAsync(Domain.BrokerInstrument instrument, DateTime startUtc, DateTime endUtc, int minutes = 1)
        {
            var ntInstrument = global::NinjaTrader.Cbi.Instrument.GetInstrument(instrument.Name);
            if (ntInstrument == null)
                throw new InvalidOperationException($"Instrument '{instrument.Name}' not found");

            // BarsRequest expects platform/local times, not UTC.
            var startPlatform = TimeZoneInfo.ConvertTimeFromUtc(startUtc, PlatformTimeZone);
            var endPlatform = TimeZoneInfo.ConvertTimeFromUtc(endUtc, PlatformTimeZone);

            _logger.Info($"[History] Requesting {minutes}m bars for {instrument.Name} | UTC: {startUtc:yyyy-MM-dd HH:mm:ss} to {endUtc:yyyy-MM-dd HH:mm:ss} | Platform: {startPlatform:yyyy-MM-dd HH:mm:ss} to {endPlatform:yyyy-MM-dd HH:mm:ss} ({PlatformTimeZone.StandardName})");

            var tcs = new TaskCompletionSource<IReadOnlyList<Domain.Bar>>();
            var barsRequest = new BarsRequest(ntInstrument, startPlatform, endPlatform)
            {
                BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = minutes },
                TradingHours = ntInstrument.MasterInstrument.TradingHours
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

                        var result = new List<Domain.Bar>();
                        for (int i = 0; i < bars.Bars.Count; i++)
                        {
                            // NinjaTrader returns bar times in platform time; convert to UTC for the domain.
                            var platformTime = DateTime.SpecifyKind(bars.Bars.GetTime(i), DateTimeKind.Unspecified);
                            var utcTime = TimeZoneInfo.ConvertTimeToUtc(platformTime, PlatformTimeZone);
                            result.Add(new Domain.Bar(
                                utcTime,
                                bars.Bars.GetOpen(i),
                                bars.Bars.GetHigh(i),
                                bars.Bars.GetLow(i),
                                bars.Bars.GetClose(i),
                                (long)bars.Bars.GetVolume(i)));
                        }

                        if (result.Count > 0)
                            _logger.Info($"[History] Received {result.Count} bars | first UTC: {result[0].Time:yyyy-MM-dd HH:mm:ss} | last UTC: {result[result.Count - 1].Time:yyyy-MM-dd HH:mm:ss}");
                        else
                            _logger.Warning("[History] BarsRequest returned 0 bars");

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
