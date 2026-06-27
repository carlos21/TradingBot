using System;
using System.Collections.Generic;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Tests
{
    public sealed class TestLogger : ILogger
    {
        public List<string> Infos { get; } = new List<string>();
        public List<string> Warnings { get; } = new List<string>();
        public List<(string Message, Exception Exception)> Errors { get; } = new List<(string, Exception)>();
        public List<string> Successes { get; } = new List<string>();
        public List<string> Debugs { get; } = new List<string>();

        public void Info(string message) => Infos.Add(message);
        public void Warning(string message) => Warnings.Add(message);
        public void Error(string message, Exception ex = null) => Errors.Add((message, ex));
        public void Success(string message) => Successes.Add(message);
        public void Debug(string message) => Debugs.Add(message);
    }
}
