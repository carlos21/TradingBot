using System;
using System.Collections.Generic;
using NinjaTrader.Data;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    internal sealed class BarSnapshot
    {
        public int Index { get; }
        public DateTime Time { get; }
        public double Open { get; }
        public double High { get; }
        public double Low { get; }
        public double Close { get; }
        public long Volume { get; }
        public long SequenceNumber { get; }

        public BarSnapshot(int index, DateTime time, double open, double high, double low, double close, long volume, long sequenceNumber)
        {
            Index = index;
            Time = time;
            Open = open;
            High = high;
            Low = low;
            Close = close;
            Volume = volume;
            SequenceNumber = sequenceNumber;
        }
    }

    internal sealed class BarStreamTracker
    {
        private int _lastSentIndex = -1;
        private DateTime _lastFormingBarTime = DateTime.MinValue;
        private long _sequenceNumber = 0;

        public void Reset(int lastSentIndex = -1)
        {
            _lastSentIndex = lastSentIndex;
            _lastFormingBarTime = DateTime.MinValue;
        }

        public IEnumerable<BarSnapshot> GetUnsentBars(BarsSeries series)
        {
            if (series == null || series.Count == 0)
                yield break;

            int lastCompleted = series.Count - 2;
            for (int i = _lastSentIndex + 1; i <= lastCompleted; i++)
            {
                yield return new BarSnapshot(
                    i,
                    series.GetTime(i),
                    series.GetOpen(i),
                    series.GetHigh(i),
                    series.GetLow(i),
                    series.GetClose(i),
                    (long)series.GetVolume(i),
                    ++_sequenceNumber
                );
            }
        }

        public void MarkSent(int index, DateTime formingBarTime)
        {
            _lastSentIndex = index;
            _lastFormingBarTime = formingBarTime;
        }

        public DateTime LastFormingBarTime => _lastFormingBarTime;
        public int LastSentIndex => _lastSentIndex;
        public long NextSequenceNumber => _sequenceNumber + 1;
    }
}
