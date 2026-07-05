"""Tests for src/gateway/integration.py.

Mocks ZeroMQ/platform-specific behaviour so no real sockets are created.
"""

from unittest.mock import MagicMock, patch

from src.config.models import AccountConfig
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.gateway.integration import (
    create_gateway_only,
    create_live_components,
    create_multi_account_live_components,
    get_platform_addresses,
)
from tests.fakes import FakeLogger


class TestCreateLiveComponents:
    def test_basic_creation(self):
        logger = FakeLogger()
        data_source, trade_executor = create_live_components(pair="MNQ", logger=logger)

        assert isinstance(data_source, ZMQDataSource)
        assert isinstance(trade_executor, ZMQTradeExecutor)
        assert data_source.pair == "MNQ"
        assert trade_executor._gateway is data_source.gateway

    def test_account_names_set_on_gateway(self):
        logger = FakeLogger()
        data_source, _ = create_live_components(
            pair="MNQ",
            logger=logger,
            account_names=["Sim101", "Sim102"],
        )
        assert data_source.gateway._account_names == ["Sim101", "Sim102"]

    def test_risk_params_passed_to_executor(self):
        logger = FakeLogger()
        _, trade_executor = create_live_components(
            pair="MNQ",
            logger=logger,
            risk_usd=100.0,
            risk_pct=1.5,
        )
        assert trade_executor._risk_usd == 100.0
        assert trade_executor._risk_pct == 1.5

    def test_custom_host_and_ports(self):
        logger = FakeLogger()
        data_source, _ = create_live_components(
            pair="ES",
            logger=logger,
            host="0.0.0.0",
            market_port=6000,
            command_port=6001,
            query_port=6002,
            heartbeat_port=6003,
        )
        config = data_source.gateway.config
        assert config.market_data_pub == "tcp://0.0.0.0:6000"
        assert config.command_pull == "tcp://0.0.0.0:6001"
        assert config.query_rep == "tcp://0.0.0.0:6002"
        assert config.heartbeat_pub == "tcp://0.0.0.0:6003"

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_returned_data_source_can_start_with_mocked_zmq(self, mock_ctx_cls):
        """Verify the created data source can be started when zmq is mocked."""
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        logger = FakeLogger()
        data_source, _ = create_live_components(pair="MNQ", logger=logger)

        data_source.start()
        assert data_source.gateway._running is True

        # data_source does not own the gateway, so stop the gateway directly
        data_source.gateway.stop()
        assert data_source.gateway._running is False


class TestCreateMultiAccountLiveComponents:
    def test_creates_multi_account_executor(self):
        logger = FakeLogger()
        account_configs = [
            AccountConfig(name="Sim101", risk_usd=50.0),
            AccountConfig(name="Sim102", risk_pct=1.0),
        ]
        data_source, executor = create_multi_account_live_components(
            pair="MNQ",
            logger=logger,
            account_configs=account_configs,
            risk_usd=100.0,
            risk_pct=2.0,
        )
        assert isinstance(data_source, ZMQDataSource)
        assert isinstance(executor, MultiAccountExecutor)
        assert executor.trade_manager is None
        assert executor.account_configs == account_configs
        assert isinstance(executor.gateway_executor, ZMQTradeExecutor)

    def test_account_names_passed_to_gateway(self):
        logger = FakeLogger()
        account_configs = [
            AccountConfig(name="A1"),
            AccountConfig(name="A2"),
        ]
        data_source, _ = create_multi_account_live_components(
            pair="MNQ",
            logger=logger,
            account_configs=account_configs,
        )
        assert data_source.gateway._account_names == ["A1", "A2"]

    def test_risk_fallbacks_passed_through(self):
        logger = FakeLogger()
        account_configs = [AccountConfig(name="Sim101", risk_usd=75.0)]
        _, executor = create_multi_account_live_components(
            pair="MNQ",
            logger=logger,
            account_configs=account_configs,
            risk_usd=200.0,
            risk_pct=3.0,
        )
        assert executor.gateway_executor._risk_usd == 200.0
        assert executor.gateway_executor._risk_pct == 3.0


class TestCreateGatewayOnly:
    def test_returns_trading_gateway(self):
        logger = FakeLogger()
        gateway = create_gateway_only(pair="ES", logger=logger)
        assert isinstance(gateway, TradingGateway)
        assert gateway.pair == "ES"
        assert gateway.config.platform_connects is True

    def test_custom_config(self):
        logger = FakeLogger()
        gateway = create_gateway_only(
            pair="MNQ",
            logger=logger,
            host="192.168.1.1",
            market_port=7000,
            command_port=7001,
            query_port=7002,
            heartbeat_port=7003,
        )
        config = gateway.config
        assert config.market_data_pub == "tcp://192.168.1.1:7000"
        assert config.command_pull == "tcp://192.168.1.1:7001"
        assert config.query_rep == "tcp://192.168.1.1:7002"
        assert config.heartbeat_pub == "tcp://192.168.1.1:7003"

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_gateway_can_start_with_mocked_zmq(self, mock_ctx_cls):
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        logger = FakeLogger()
        gateway = create_gateway_only(pair="MNQ", logger=logger)
        gateway.start()
        assert gateway._running is True
        gateway.stop()
        assert gateway._running is False


class TestGetPlatformAddresses:
    def test_default_addresses(self):
        addrs = get_platform_addresses()
        assert addrs == {
            "market_data": "tcp://127.0.0.1:5555",
            "commands": "tcp://127.0.0.1:5556",
            "queries": "tcp://127.0.0.1:5557",
            "heartbeat": "tcp://127.0.0.1:5558",
        }

    def test_custom_addresses(self):
        addrs = get_platform_addresses(
            host="0.0.0.0",
            market_port=9000,
            command_port=9001,
            query_port=9002,
            heartbeat_port=9003,
        )
        assert addrs == {
            "market_data": "tcp://0.0.0.0:9000",
            "commands": "tcp://0.0.0.0:9001",
            "queries": "tcp://0.0.0.0:9002",
            "heartbeat": "tcp://0.0.0.0:9003",
        }

    def test_gateway_config_matches_integration_addresses(self):
        """Ensure GatewayConfig.get_platform_addresses stays in sync."""
        config = GatewayConfig()
        addrs = get_platform_addresses()
        assert config.get_platform_addresses() == addrs
