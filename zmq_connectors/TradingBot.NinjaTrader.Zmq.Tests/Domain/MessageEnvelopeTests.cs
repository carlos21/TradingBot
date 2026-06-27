using System;
using FluentAssertions;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;
using Xunit;

namespace TradingBot.NinjaTrader.Zmq.Tests.Domain
{
    public class MessageEnvelopeTests
    {
        [Fact]
        public void Create_Should_Assign_MsgType_And_Payload()
        {
            var payload = new JObject { ["instrument"] = "MNQ 09-25" };
            var envelope = MessageEnvelope.Create(MessageType.OrderOpen, payload, seqNum: 5);

            envelope.MsgType.Should().Be(MessageType.OrderOpen);
            envelope.SeqNum.Should().Be(5);
            envelope.Payload.Should().BeEquivalentTo(payload);
            envelope.Timestamp.Should().BeGreaterThan(0);
        }

        [Fact]
        public void Create_With_Null_Payload_Should_Use_Empty_Object()
        {
            var envelope = MessageEnvelope.Create(MessageType.Heartbeat, null);

            envelope.Payload.Should().NotBeNull();
            envelope.Payload.Count.Should().Be(0);
        }

        [Fact]
        public void Create_With_Null_MsgType_Should_Throw()
        {
            Action act = () => MessageEnvelope.Create(null, new JObject());
            act.Should().Throw<ArgumentNullException>().Which.ParamName.Should().Be("msgType");
        }

        [Fact]
        public void ToJson_Should_Return_Serialized_Envelope()
        {
            var payload = new JObject { ["trade_id"] = "abc" };
            var envelope = MessageEnvelope.Create(MessageType.OrderClose, payload, seqNum: 1);
            var json = envelope.ToJson();

            json.Should().Contain("\"msg_type\":\"order_close\"");
            json.Should().Contain("\"trade_id\":\"abc\"");
            json.Should().Contain("\"seq_num\":1");
        }
    }
}
