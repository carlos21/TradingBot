// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: AuditRequestHandler
// Handles AUDIT_REQUEST commands — returns last N bars for verification.
// Does NOT trigger refresh lifecycle; purely read-only audit.
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles AUDIT_REQUEST commands.
    /// Creates a temporary BarsRequest to fetch the last N 1m bars
    /// and sends them back as an AuditResponse.  This is a read-only
    /// operation that does not mutate strategy state.
    /// </summary>
    internal sealed class AuditRequestHandler : ICommandHandler
    {
        public string CommandType => MessageType.AuditRequest;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Func<string> _getInstrument;

        public AuditRequestHandler(ZmqNetwork network, ILogger logger, Func<string> getInstrument)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _getInstrument = getInstrument ?? throw new ArgumentNullException(nameof(getInstrument));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                int barsBack = payload?["bars_back"]?.Value<int>() ?? 60;
                if (barsBack < 1) barsBack = 60;
                if (barsBack > 5000) barsBack = 5000; // Safety cap

                var instrumentName = payload?["instrument"]?.Value<string>() ?? _getInstrument();
                if (string.IsNullOrWhiteSpace(instrumentName))
                {
                    _logger.Error("AUDIT REQUEST: missing instrument in payload and no active instrument configured");
                    return false;
                }

                _logger.Info($"AUDIT REQUEST: returning last {barsBack} bars for {instrumentName}");

                var instrument = Instrument.GetInstrument(instrumentName);
                if (instrument == null)
                {
                    _logger.Error($"Instrument '{instrumentName}' not found for audit");
                    return false;
                }

                // Request one extra bar so we can drop the forming (partial) bar at the end.
                // Python only caches completed bars from ZMQ BAR messages, so including the
                // forming bar in the audit response causes a systematic missing=1 extra=1 drift.
                var barsRequest = new BarsRequest(instrument, barsBack + 1)
                {
                    BarsPeriod = new BarsPeriod { BarsPeriodType = BarsPeriodType.Minute, Value = 1 },
                    TradingHours = TradingHours.Get("Default 24 x 7")
                };

                barsRequest.Request((bars, errorCode, errorMessage) =>
                {
                    try
                    {
                        if (errorCode != ErrorCode.NoError)
                        {
                            _logger.Error($"Audit BarsRequest failed: {errorMessage}");
                            return;
                        }

                        var batch = new List<JObject>();
                        if (bars?.Bars != null)
                        {
                            // Skip the last index — it is the forming/partial bar, which Python
                            // does not yet have in its cache. We only return completed bars.
                            int count = Math.Max(0, bars.Bars.Count - 1);
                            for (int i = 0; i < count; i++)
                            {
                                batch.Add(new JObject
                                {
                                    ["time"] = ToUnixSeconds(bars.Bars.GetTime(i)),
                                    ["open"] = bars.Bars.GetOpen(i),
                                    ["high"] = bars.Bars.GetHigh(i),
                                    ["low"] = bars.Bars.GetLow(i),
                                    ["close"] = bars.Bars.GetClose(i),
                                    ["volume"] = (long)bars.Bars.GetVolume(i),
                                    ["pair"] = instrumentName.Split(' ')[0]
                                });
                            }
                        }

                        _network?.SendAuditResponse(instrumentName.Split(' ')[0], batch);
                        _logger.Info($"AUDIT RESPONSE: sent {batch.Count} completed bars (requested {barsBack + 1}, dropped forming bar)");
                    }
                    catch (Exception callbackEx)
                    {
                        _logger.Error("Audit BarsRequest callback error", callbackEx);
                    }
                    finally
                    {
                        barsRequest?.Dispose();
                    }
                });

                return true;
            }
            catch (Exception ex)
            {
                _logger.Error("Audit request failed", ex);
                _network?.SendError("ninjatrader", "audit_failed", ex.Message);
                return false;
            }
        }

        private static double ToUnixSeconds(DateTime dt) =>
            (dt.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
    }
}
