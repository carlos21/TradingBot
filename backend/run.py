#!/usr/bin/env python3
"""Thin entry point for running the TradingBot backend."""

import os
import sys

# Make `from src...` imports work when running from the repo root or backend/.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BACKEND_DIR, "src"))

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
