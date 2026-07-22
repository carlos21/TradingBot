using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class RefreshRequestHandler : ICommandHandler
    {
        public string CommandType => MessageType.RefreshRequest;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IBarHistoryService _barHistoryService;
        private readonly IConnectorClock _clock;
        private readonly ZmqConfiguration _config;

        public RefreshRequestHandler(
            IZmqNetwork network,
            ILogger logger,
            IInstrumentProvider instrumentProvider,
            IBarHistoryService barHistoryService,
            IConnectorClock clock,
            ZmqConfiguration config)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _barHistoryService = barHistoryService ?? throw new ArgumentNullException(nameof(barHistoryService));
            _clock = clock ?? throw new ArgumentNullException(nameof(clock));
            _config = config ?? throw new ArgumentNullException(nameof(config));
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
                var instrumentName = payload?["instrument"]?.Value<string>();
                var days = payload?["days"]?.Value<int>() ?? _config.HistoryDays;
                if (string.IsNullOrEmpty(instrumentName))
                {
                    _logger.Error("Instrument missing in REFRESH_REQUEST");
                    return;
                }

                var instrument = _instrumentProvider.GetInstrument(instrumentName);
                if (instrument == null)
                {
                    _logger.Error($"Instrument '{instrumentName}' not found");
                    return;
                }

                var pair = instrumentName.Split(' ')[0];
                _network.SendRefreshStart(pair);
                _logger.Info("Sending refresh_start");

                var endUtc = _clock.UtcNow;
                var startUtc = endUtc.AddDays(-Math.Min(days, 30));
                var bars = await _barHistoryService.RequestHistoryAsync(instrument, startUtc, endUtc);

                if (bars == null || bars.Count == 0)
                {
                    _logger.Warning($"No historical bars found for {instrumentName}");
                    _network.SendHistoryEnd(pair);
                    return;
                }

                if (bars.Count > 0)
                    _logger.Info($"[History] Sending {bars.Count} bars for {instrumentName} | first UTC: {bars[0].Time:yyyy-MM-dd HH:mm:ss} | last UTC: {bars[bars.Count - 1].Time:yyyy-MM-dd HH:mm:ss}");

                var trimmed = TrimToLastSession(bars, days);
                var jBars = trimmed.Select(bar => new JObject
                {
                    ["time"] = ToUnixSeconds(bar.Time),
                    ["open"] = bar.Open,
                    ["high"] = bar.High,
                    ["low"] = bar.Low,
                    ["close"] = bar.Close,
                    ["volume"] = bar.Volume,
                    ["pair"] = pair
                }).ToList();

                var batch = new List<JObject>();
                foreach (var bar in jBars)
                {
                    batch.Add(bar);
                    if (batch.Count >= _config.BatchSize)
                    {
                        _network.SendHistoryBatch(pair, batch, days);
                        batch.Clear();
                    }
                }
                if (batch.Count > 0)
                    _network.SendHistoryBatch(pair, batch, days);

                _network.SendHistoryEnd(pair);
                _logger.Info($"Sent {jBars.Count} historical bars for {pair}");
            }
            catch (Exception ex)
            {
                _logger.Error("SendHistory error", ex);
                _network.SendError("ninjatrader", "history_load_failed", "Failed to load history", ex.Message);
            }
        }

        private IReadOnlyList<Bar> TrimToLastSession(IReadOnlyList<Bar> bars, int days)
        {
            if (bars == null || bars.Count < 2) return bars;
            int maxBars = days * 24 * 60;
            if (bars.Count <= maxBars) return bars;
            return bars.Skip(bars.Count - maxBars).ToList();
        }

        private static double ToUnixSeconds(DateTime dt)
        {
            var utc = dt.Kind == DateTimeKind.Utc ? dt : dt.ToUniversalTime();
            return (utc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
        }
    }
}
