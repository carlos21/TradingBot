"""TSI (True Strength Index) indicator calculations.

Extracted from triggers.py so both line-based and line-less strategies can
reuse the same pure math without coupling to trigger machinery.
"""


def calculate_ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    if length <= 0:
        raise ValueError(f"EMA length must be positive, got {length}")
    alpha = 2 / (length + 1)
    ema_values = [values[0]]
    for price in values[1:]:
        prev_ema = ema_values[-1]
        new_ema = (price * alpha) + (prev_ema * (1 - alpha))
        ema_values.append(new_ema)
    return ema_values


def calculate_tsi_series(
    closes: list[float],
    long_len: int = 6,
    short_len: int = 13,
    sig_len: int = 4,
) -> tuple[list[float], list[float]]:
    """Return (tsi_values, signal_values) for the given close prices.

    :param closes: list of close prices
    :param long_len: first smoothing length (default 6)
    :param short_len: second smoothing length (default 13)
    :param sig_len: signal line EMA length (default 4)
    :returns: tuple of (tsi line, signal line)
    """
    if long_len <= 0 or short_len <= 0 or sig_len <= 0:
        raise ValueError(
            f"TSI lengths must be positive, got long_len={long_len}, "
            f"short_len={short_len}, sig_len={sig_len}"
        )
    if len(closes) < long_len + short_len + sig_len:
        return [], []

    pc = [0.0] * len(closes)
    abs_pc = [0.0] * len(closes)
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        pc[i] = diff
        abs_pc[i] = abs(diff)

    ema_pc_2 = calculate_ema(calculate_ema(pc, long_len), short_len)
    ema_apc_2 = calculate_ema(calculate_ema(abs_pc, long_len), short_len)

    tsi_values = []
    for val, abs_val in zip(ema_pc_2, ema_apc_2, strict=False):
        tsi_values.append(100.0 * (val / abs_val) if abs_val != 0 else 0.0)

    signal_values = calculate_ema(tsi_values, sig_len)
    return tsi_values, signal_values
