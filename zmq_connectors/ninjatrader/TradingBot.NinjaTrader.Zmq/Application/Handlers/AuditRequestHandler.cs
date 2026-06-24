using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class AuditRequestHandler : ICommandHandler
    {
        public string CommandType => MessageType.AuditRequest;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IStreamingCoordinator _streamingCoordinator;
        private readonly IConnectorClock _clock;

        public AuditRequestHandler(
            IZmqNetwork network,
            ILogger logger,
            IInstrumentProvider instrumentProvider,
            IBarHistoryService barHistoryService,
            IStreamingCoordinator streamingCoordinator,
            IConnectorClock clock)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _barHistoryService = barHistoryService ?? throw new ArgumentNullException(nameof(barHistoryService));
            _streamingCoordinator = streamingCoordinator ?? throw new ArgumentNullException(nameof(streamingCoordinator));
            _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        }

        public bool Handle(JObject payload)
        {
            _ = HandleAsync(payload);
            return true;
        }

        private async Task HandleAsync(JObject payload)
        {
            try
            {
                int barsBack = payload?["bars_back"]?.Value<int>() ?? 60;
                if (barsBack < 1) barsBack = 60;
                if (barsBack > 5000) barsBack = 5000;

                var instrumentName = payload?["instrument"]?.Value<string>() ?? _streamingCoordinator.CurrentInstrument;
                if (string.IsNullOrWhiteSpace(instrumentName))
                {
                    _logger.Error("AUDIT REQUEST: missing instrument in payload and no active instrument configured");
                    return;
                }

                _logger.Info($"AUDIT REQUEST: returning last {barsBack} bars for {instrumentName}");

                var instrument = _instrumentProvider.GetInstrument(instrumentName);
                if (instrument == null)
                {
                    _logger.Error($"Instrument '{instrumentName}' not found for audit");
                    return;
                }

                var endUtc = _clock.UtcNow;
                var startUtc = endUtc.AddMinutes(-(barsBack + 5)); // request a small buffer
                var bars = await _barHistoryService.RequestHistoryAsync(instrument, startUtc, endUtc);

                var pair = instrumentName.Split(' ')[0];
                var batch = new List<JObject>();
                if (bars != null)
                {
                    int count = Math.Max(0, bars.Count - 1);
                    var recent = bars.Skip(Math.Max(0, count - barsBack)).Take(barsBack).ToList();
                    foreach (var bar in recent)
                    {
                        batch.Add(new JObject
                        {
                            ["time"] = ToUnixSeconds(bar.Time),
                            ["open"] = bar.Open,
                            ["high"] = bar.High,
                            ["low"] = bar.Low,
                            ["close"] = bar.Close,
                            ["volume"] = bar.Volume,
                            ["pair"] = pair
                        });
                    }
                }

                _network.SendAuditResponse(pair, batch);
                _logger.Info($"AUDIT RESPONSE: sent {batch.Count} completed bars for {pair}");
            }
            catch (Exception ex)
            {
                _logger.Error("Audit request failed", ex);
                _network.SendError("ninjatrader", "audit_failed", ex.Message);
            }
        }

        private static double ToUnixSeconds(DateTime dt) =>
            (dt.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
    }
}
