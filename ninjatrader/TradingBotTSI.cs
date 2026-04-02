// TradingBotTSI.cs — NinjaScript Indicator
// Replicates the exact TSI(6, 13, 4) calculation from the Python TradingBot strategy.
//
// Algorithm (mirrors triggers.py::_calculate_tsi_series):
//   pc[i]      = close[i] - close[i-1]          (price change, seeded with 0)
//   abs_pc[i]  = |pc[i]|
//   ema1_pc    = EMA(pc,     LongLen=6)
//   ema2_pc    = EMA(ema1_pc, ShortLen=13)
//   ema1_apc   = EMA(abs_pc,  LongLen=6)
//   ema2_apc   = EMA(ema1_apc, ShortLen=13)
//   TSI        = 100 * (ema2_pc / ema2_apc)
//   Signal     = EMA(TSI, SignalLen=4)
//
// Installation:
//   1. NinjaTrader → New → NinjaScript Editor → Indicators folder → Add new file
//   2. Replace the generated content with this file, compile (F5)
//   3. Apply to a 5m chart (or whichever timeframe the strategy is configured for)

#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class TradingBotTSI : Indicator
    {
        // Intermediate series — match Python's in-order EMA chaining exactly
        private Series<double> pc;
        private Series<double> abspc;
        private Series<double> ema1pc;
        private Series<double> ema2pc;
        private Series<double> ema1apc;
        private Series<double> ema2apc;
        private Series<double> tsiRaw;

        // Arrow brushes — created once and frozen to avoid GDI pressure
        private SolidColorBrush bullBrush;
        private SolidColorBrush bearBrush;

        // Plot indices
        private const int PLOT_TSI    = 0;
        private const int PLOT_SIGNAL = 1;
        private const int PLOT_ZERO   = 2;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TradingBotTSI";
                Description = "TSI(6,13,4) — exact replication of Python TradingBot strategy";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = false;
                DisplayInDataBox        = true;
                DrawOnPricePanel        = true;   // arrows land on the price panel; TSI plots stay in sub-panel
                DrawHorizontalGridLines = true;
                DrawVerticalGridLines   = false;
                ScaleJustification      = NinjaTrader.Gui.Chart.ScaleJustification.Right;

                // Default parameters — match Python prod_config
                LongLen   = 6;
                ShortLen  = 13;
                SignalLen = 4;

                // Blue TSI line
                AddPlot(new Stroke(Brushes.DodgerBlue, 2), PlotStyle.Line, "TSI");
                // Red Signal line
                AddPlot(new Stroke(Brushes.OrangeRed, 2), PlotStyle.Line, "Signal");
                // Gray dotted zero line
                AddPlot(new Stroke(Brushes.Gray, DashStyleHelper.Dot, 1), PlotStyle.Line, "Zero");
            }
            else if (State == State.DataLoaded)
            {
                pc      = new Series<double>(this, MaximumBarsLookBack.Infinite);
                abspc   = new Series<double>(this, MaximumBarsLookBack.Infinite);
                ema1pc  = new Series<double>(this, MaximumBarsLookBack.Infinite);
                ema2pc  = new Series<double>(this, MaximumBarsLookBack.Infinite);
                ema1apc = new Series<double>(this, MaximumBarsLookBack.Infinite);
                ema2apc = new Series<double>(this, MaximumBarsLookBack.Infinite);
                tsiRaw  = new Series<double>(this, MaximumBarsLookBack.Infinite);

                // #00E676 (bullish green) and #FF1744 (bearish red) — match chart.html exactly
                bullBrush = new SolidColorBrush(Color.FromRgb(0x00, 0xE6, 0x76));
                bullBrush.Freeze();
                bearBrush = new SolidColorBrush(Color.FromRgb(0xFF, 0x17, 0x44));
                bearBrush.Freeze();
            }
        }

        protected override void OnBarUpdate()
        {
            // Always paint zero line
            Values[PLOT_ZERO][0] = 0;

            // --- Bar 0: seed everything with 0 (matches Python pc[0] = 0) ---
            if (CurrentBar == 0)
            {
                pc[0]      = 0;
                abspc[0]   = 0;
                ema1pc[0]  = 0;
                ema2pc[0]  = 0;
                ema1apc[0] = 0;
                ema2apc[0] = 0;
                tsiRaw[0]  = 0;
                Values[PLOT_TSI][0]    = 0;
                Values[PLOT_SIGNAL][0] = 0;
                return;
            }

            // Pre-compute EMA smoothing factors
            double alphaL   = 2.0 / (LongLen   + 1);
            double alphaS   = 2.0 / (ShortLen  + 1);
            double alphaSig = 2.0 / (SignalLen  + 1);

            // --- Price change ---
            double diff = Close[0] - Close[1];
            pc[0]    = diff;
            abspc[0] = Math.Abs(diff);

            // --- EMA(pc, LongLen) ---
            ema1pc[0] = pc[0] * alphaL + ema1pc[1] * (1.0 - alphaL);

            // --- EMA(EMA(pc, LongLen), ShortLen) ---
            ema2pc[0] = ema1pc[0] * alphaS + ema2pc[1] * (1.0 - alphaS);

            // --- EMA(abs_pc, LongLen) ---
            ema1apc[0] = abspc[0] * alphaL + ema1apc[1] * (1.0 - alphaL);

            // --- EMA(EMA(abs_pc, LongLen), ShortLen) ---
            ema2apc[0] = ema1apc[0] * alphaS + ema2apc[1] * (1.0 - alphaS);

            // --- TSI = 100 * (numerator / denominator) ---
            tsiRaw[0] = ema2apc[0] != 0.0
                ? 100.0 * (ema2pc[0] / ema2apc[0])
                : 0.0;

            Values[PLOT_TSI][0] = tsiRaw[0];

            // --- Signal = EMA(TSI, SignalLen) ---
            Values[PLOT_SIGNAL][0] = tsiRaw[0] * alphaSig + Values[PLOT_SIGNAL][1] * (1.0 - alphaSig);

            // --- Crossover arrows on the price panel (mirrors MarketManager.js logic) ---
            // Need at least 2 bars so we have a previous TSI and Signal value
            if (CurrentBar < 2) return;

            double currTsi = tsiRaw[0];
            double prevTsi = tsiRaw[1];
            double currSig = Values[PLOT_SIGNAL][0];
            double prevSig = Values[PLOT_SIGNAL][1];

            // Bullish cross: TSI crosses ABOVE Signal — green arrow below bar
            if (prevTsi <= prevSig && currTsi > currSig)
            {
                Draw.ArrowUp(this, "bull_" + CurrentBar, false, 0,
                    Low[0] - 2 * TickSize, bullBrush);
            }
            // Bearish cross: TSI crosses BELOW Signal — red arrow above bar
            else if (prevTsi >= prevSig && currTsi < currSig)
            {
                Draw.ArrowDown(this, "bear_" + CurrentBar, false, 0,
                    High[0] + 2 * TickSize, bearBrush);
            }
        }

        #region Properties

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Long Length", Description = "First EMA period (default 6)", Order = 1, GroupName = "TSI Parameters")]
        public int LongLen { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Short Length", Description = "Second EMA period (default 13)", Order = 2, GroupName = "TSI Parameters")]
        public int ShortLen { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Signal Length", Description = "Signal EMA period (default 4)", Order = 3, GroupName = "TSI Parameters")]
        public int SignalLen { get; set; }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> TSI => Values[PLOT_TSI];

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> Signal => Values[PLOT_SIGNAL];

        #endregion
    }
}

#region NinjaScript generated code. Neither change nor remove.
namespace NinjaTrader.NinjaScript.Indicators
{
    public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
    {
        private TradingBotTSI[] cacheTradingBotTSI;
        public TradingBotTSI TradingBotTSI(int longLen, int shortLen, int signalLen)
        {
            return TradingBotTSI(Input, longLen, shortLen, signalLen);
        }

        public TradingBotTSI TradingBotTSI(ISeries<double> input, int longLen, int shortLen, int signalLen)
        {
            if (cacheTradingBotTSI != null)
                for (int idx = 0; idx < cacheTradingBotTSI.Length; idx++)
                    if (cacheTradingBotTSI[idx] != null && cacheTradingBotTSI[idx].LongLen == longLen
                        && cacheTradingBotTSI[idx].ShortLen == shortLen && cacheTradingBotTSI[idx].SignalLen == signalLen
                        && cacheTradingBotTSI[idx].EqualsInput(input))
                        return cacheTradingBotTSI[idx];
            return CacheIndicator<TradingBotTSI>(new TradingBotTSI(){ LongLen = longLen, ShortLen = shortLen, SignalLen = signalLen }, input, ref cacheTradingBotTSI);
        }
    }
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
    public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
    {
        public Indicators.TradingBotTSI TradingBotTSI(int longLen, int shortLen, int signalLen)
        {
            return indicator.TradingBotTSI(Input, longLen, shortLen, signalLen);
        }

        public Indicators.TradingBotTSI TradingBotTSI(ISeries<double> input, int longLen, int shortLen, int signalLen)
        {
            return indicator.TradingBotTSI(input, longLen, shortLen, signalLen);
        }
    }
}

namespace NinjaTrader.NinjaScript.Strategies
{
    public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
    {
        public Indicators.TradingBotTSI TradingBotTSI(int longLen, int shortLen, int signalLen)
        {
            return indicator.TradingBotTSI(Input, longLen, shortLen, signalLen);
        }

        public Indicators.TradingBotTSI TradingBotTSI(ISeries<double> input, int longLen, int shortLen, int signalLen)
        {
            return indicator.TradingBotTSI(input, longLen, shortLen, signalLen);
        }
    }
}
#endregion
