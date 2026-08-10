"""Helper to find free ephemeral TCP ports for ZMQ test fixtures.

Avoids port collisions in CI or when running tests in parallel.
"""

from __future__ import annotations

import socket


def get_free_ports(count: int = 4) -> list[int]:
    """Return `count` distinct free TCP port numbers.

    Every socket is kept open until all ports have been allocated, so the
    OS cannot hand the same ephemeral port out twice within one call (the
    previous bind-release-rebind loop could return duplicates, causing
    ``ZMQError: Address already in use`` when two channels got the same
    port).  There is still a narrow race window where another process
    could grab a port between release and re-use, but in practice this is
    negligible for localhost test fixtures.
    """
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            sockets.append(s)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:
            s.close()
