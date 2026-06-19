"""Pure input validation for line-controller endpoints.

Keeps HTTP concerns (abort, JSON) in the controller and validation rules in
this module so they can be reused and unit-tested independently.
"""

from __future__ import annotations


class LineInputValidator:
    """Validates and coerces inputs for line add/update operations."""

    @staticmethod
    def validate_price(value: object) -> float:
        """Return a positive float price or raise ValueError."""
        try:
            price = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("price must be a numeric value") from exc
        if price <= 0:
            raise ValueError("price must be positive")
        return price

    @staticmethod
    def validate_creation_timestamp(value: object) -> float:
        """Return a numeric timestamp or raise ValueError."""
        try:
            timestamp = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("creation_timestamp must be a numeric timestamp") from exc
        return timestamp
