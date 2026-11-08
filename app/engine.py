"""Deterministic execution and content-addressed inputs.

Only the two reviewed, bundled model sources can execute. Uploaded code is never
accepted. Decimal arithmetic uses an explicit context independent of thread state.
"""
import hashlib
import json
import platform
import sys
from datetime import date
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = {"equal-weight": ROOT / "app/model_v1.py", "recency-weighted": ROOT / "app/model_v2.py"}


class ResearchError(ValueError):
    pass


class IntegrityError(ResearchError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def environment():
    return {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "engine_sha256": digest(Path(__file__).read_bytes()),
        "arithmetic": "decimal / precision 34 / ROUND_HALF_EVEN",
        "platform": platform.system(),
    }


def normalise_rows(rows):
    if not isinstance(rows, list) or not 4 <= len(rows) <= 10000:
        raise ResearchError("Supply between 4 and 10,000 observations.")
    clean = []
    previous = ""
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"date", "value"}:
            raise ResearchError("Each observation must have exactly date and value fields.")
        try:
            day = date.fromisoformat(row["date"]).isoformat()
            text = str(row["value"])
            if len(text) > 64:
                raise ValueError("Value is too long")
            number = Decimal(text)
        except (TypeError, ValueError, InvalidOperation):
            raise ResearchError("Observations require ISO dates and finite decimal values.") from None
        if not number.is_finite() or number.copy_abs() > 100:
            raise ResearchError("Yield values must be finite and between -100 and 100 percent.")
        if number.as_tuple().exponent < -12:
            raise ResearchError("Yield values support at most 12 decimal places.")
        if day <= previous:
            raise ResearchError("Dates must be unique and in increasing order.")
        previous = day
        value = format(number, "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        clean.append({"date": day, "value": "0" if number.is_zero() else value})
    return clean


def normalise_config(config, count):
    if not isinstance(config, dict) or set(config) != {"lookback", "horizon"}:
        raise ResearchError("Configuration requires lookback and horizon only.")
    lookback, horizon = config["lookback"], config["horizon"]
    if type(lookback) is not int or not 2 <= lookback <= min(120, count - 1):
        raise ResearchError(f"Lookback must be an integer from 2 to {min(120, count - 1)}.")
    if type(horizon) is not int or not 1 <= horizon <= 20:
        raise ResearchError("Forecast horizon must be an integer from 1 to 20 observation steps.")
    return {"lookback": lookback, "horizon": horizon}


def execute(source, rows, config):
    trusted = {digest(path.read_bytes()) for path in MODELS.values()}
    if digest(source) not in trusted:
        raise IntegrityError("Model source is not one of the reviewed bundled implementations.")
    rows = normalise_rows(rows)
    config = normalise_config(config, len(rows))
    namespace = {}
    exec(compile(source.decode("utf-8"), "<captured-reviewed-model>", "exec"), namespace)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        return namespace["forecast"](rows, config)
