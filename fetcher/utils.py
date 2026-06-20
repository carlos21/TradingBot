"""Shared helpers for fetcher providers."""

from datetime import datetime, timezone


_UTC = timezone.utc


def ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime, treating naive datetimes as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)


def symbol_to_pair(symbol: str) -> str:
    """Map a provider symbol to the internal pair name.

    Examples:
        "NQ=F"       -> "MNQ"
        "NQ:XCME"    -> "MNQ"
        "NQ1!"       -> "MNQ"
        "NQ.FUT"     -> "MNQ"
        "NQH25:XCME" -> "NQH25"
        "AAPL"       -> "AAPL"
    """
    # Strip provider-specific suffixes/exchanges.
    base = symbol.split("=")[0].split(":")[0].split(".")[0]
    # Alpaca uses a "1!" continuous-contract suffix; strip it first.
    if base.endswith("1!"):
        pair = base[:-2]
    else:
        # Remove contract-month letters/digits (e.g., H25).
        pair = base.rstrip("FGHMNQUVXZ0123456789")
    return pair.upper() or base.upper()
