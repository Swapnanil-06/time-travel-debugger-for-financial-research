"""Linearly weighted drift: newer observed changes receive greater weight."""
from decimal import Decimal, localcontext


def forecast(rows, config):
    with localcontext() as ctx:
        ctx.prec = 34
        values = [Decimal(row["value"]) for row in rows]
        changes = [b - a for a, b in zip(values, values[1:])][-config["lookback"]:]
        weights = [Decimal(i + 1) for i in range(len(changes))]
        total = sum(weights)
        drift = sum(x * w for x, w in zip(changes, weights)) / total
        variance = sum(w * (x - drift) ** 2 for x, w in zip(changes, weights)) / total
        return {
            "last_yield": str(values[-1]),
            "drift_bps": str(drift * 100),
            "daily_vol_bps": str(variance.sqrt() * 100),
            "forecast": [str(values[-1] + drift * step) for step in range(1, config["horizon"] + 1)],
            "unit": "percent",
        }
