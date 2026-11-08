"""Equal-weight drift in Treasury yields; an illustrative model, not an alpha claim."""
from decimal import Decimal, localcontext


def forecast(rows, config):
    with localcontext() as ctx:
        ctx.prec = 34
        values = [Decimal(row["value"]) for row in rows]
        changes = [b - a for a, b in zip(values, values[1:])][-config["lookback"]:]
        drift = sum(changes) / Decimal(len(changes))
        variance = sum((x - drift) ** 2 for x in changes) / Decimal(len(changes))
        return {
            "last_yield": str(values[-1]),
            "drift_bps": str(drift * 100),
            "daily_vol_bps": str(variance.sqrt() * 100),
            "forecast": [str(values[-1] + drift * step) for step in range(1, config["horizon"] + 1)],
            "unit": "percent",
        }
