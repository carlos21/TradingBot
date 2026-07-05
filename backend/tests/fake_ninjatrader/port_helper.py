"""Helper to find free ephemeral TCP ports for ZMQ test fixtures.

Avoids port collisions in CI or when running tests in parallel.
"""

from __future__ import annotations

import socket


def get_free_ports(count: int = 4) -> list[int]:
    """Return `count` free TCP port numbers.

    Each port is found by binding to port 0 and immediately releasing it.
    There is a narrow race window where another process could grab the port
    between release and re-use, but in practice this is negligible for
    localhost test fixtures.
    """
    ports: list[int] = []
    for _ in range(count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    return ports
