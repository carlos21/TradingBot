#!/usr/bin/env python3
"""
Mock NinjaTrader client for testing the NinjaTraderDataSource.

Usage:
    python scripts/mock_nt_client.py [--host HOST] [--port PORT] [--bars N] [--tick-interval SECS]
"""
import argparse
import json
import socket
import time
import random
from datetime import datetime, timezone


def send_msg(sock: socket.socket, msg: dict):
    sock.sendall((json.dumps(msg) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description="Mock NinjaTrader client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8889)
    parser.add_argument("--bars", type=int, default=500, help="Number of historical 1m bars")
    parser.add_argument("--tick-interval", type=float, default=0.5, help="Seconds between ticks")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.host, args.port))
    print(f"Connected to {args.host}:{args.port}")

    # Send historical bars
    base_time = int(datetime.now(timezone.utc).timestamp()) - args.bars * 60
    price = 18000.0

    print(f"Sending {args.bars} historical bars...")
    for i in range(args.bars):
        t = base_time + i * 60
        change = random.uniform(-5, 5)
        o = price
        h = price + abs(random.uniform(0, 3))
        l = price - abs(random.uniform(0, 3))
        price += change
        c = price
        send_msg(sock, {
            "type": "BAR",
            "time": t,
            "open": round(o, 2),
            "high": round(max(o, c, h), 2),
            "low": round(min(o, c, l), 2),
            "close": round(c, 2),
            "volume": random.randint(100, 5000),
            "pair": "NQ",
        })
        time.sleep(0.001)  # small delay to avoid overwhelming

    # Signal end of history
    send_msg(sock, {"type": "HISTORY_END"})
    print("History complete, sending live ticks...")

    # Send live ticks + heartbeats
    last_heartbeat = time.time()
    try:
        while True:
            # Tick
            price += random.uniform(-2, 2)
            send_msg(sock, {
                "type": "TICK",
                "time": int(datetime.now(timezone.utc).timestamp()),
                "price": round(price, 2),
                "volume": 1,
                "pair": "NQ",
            })

            # Heartbeat every 5s
            if time.time() - last_heartbeat >= 5:
                send_msg(sock, {
                    "type": "HEARTBEAT",
                    "time": int(datetime.now(timezone.utc).timestamp()),
                })
                last_heartbeat = time.time()

            time.sleep(args.tick_interval)
    except KeyboardInterrupt:
        print("\nStopping mock client")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
