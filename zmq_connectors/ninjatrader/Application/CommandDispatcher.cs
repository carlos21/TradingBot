// ═══════════════════════════════════════════════════════════════════════
// Application Layer: Command Dispatcher
// Chain of Responsibility + Strategy pattern for command handling
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Chain of Responsibility + Strategy pattern for command handling.
    /// Decouples command routing from command implementation.
    /// </summary>
    internal sealed class CommandDispatcher
    {
        private readonly Dictionary<string, ICommandHandler> _handlers = new Dictionary<string, ICommandHandler>();
        private readonly ILogger _logger;

        public CommandDispatcher(ILogger logger)
        {
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        }

        public void Register(ICommandHandler handler)
        {
            if (handler == null) throw new ArgumentNullException(nameof(handler));
            _handlers[handler.CommandType] = handler;
        }

        public void Dispatch(MessageEnvelope envelope)
        {
            if (envelope == null) return;

            if (_handlers.TryGetValue(envelope.MsgType, out var handler))
            {
                try
                {
                    handler.Handle(envelope.Payload);
                }
                catch (Exception ex)
                {
                    _logger.Error($"Command handler failed for {envelope.MsgType}", ex);
                }
            }
            else
            {
                _logger.Warning($"Unknown command: {envelope.MsgType}");
            }
        }
    }
}
