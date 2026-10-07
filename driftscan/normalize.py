"""Making two stores' values comparable without lying about them.

This is the part that decides whether a drift detector is useful or just noisy.
ClickHouse and Postgres render the same logical value differently, and a
detector that hashes raw strings reports those differences as drift. After the
third false alarm nobody reads the report, which is worse than not having one.

Every normalisation here is LOSSY BY DESIGN - it deliberately erases a
distinction. So each one is named, and the report says which were applied. A
normalisation you cannot see is indistinguishable from a bug.
"""

from __future__ import annotations

import datetime as _dt
import decimal
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    why: str


# The rules, in the order they are tried. Keep this list short and defensible:
# every entry is a claim that two differently-rendered values ARE the same value.
RULES = (
    Rule("null-unification",
         "ClickHouse Nullable columns and Postgres NULL both mean absent, but a "
         "non-Nullable ClickHouse column substitutes a type default (0, '') "
         "instead. Only applied when the column is declared nullable on both "
         "sides - otherwise '' and NULL are genuinely different values."),
    Rule("decimal-scale",
         "Decimal(18,4) 1.5 renders as '1.5000' in one store and '1.5' in the "
         "other. Compared as exact decimals, never as floats."),
    Rule("timestamp-utc",
         "Timestamps are normalised to UTC and truncated to the COARSER of the "
         "two precisions. ClickHouse DateTime is second-precision; Postgres "
         "timestamptz is microsecond. Comparing them at microsecond precision "
         "reports every row as drifted."),
    Rule("bool-int",
         "ClickHouse has no bool; it stores UInt8 0/1 where Postgres stores "
         "false/true."),
)


def normalize(value, *, nullable: bool = False, scale: int | None = None,
              ts_precision: str = "second") -> tuple[object, tuple[str, ...]]:
    """Return (comparable value, names of rules that fired).

    Returning the rule names rather than just the value is the whole point: the
    report can then say "these two rows are equal ONCE you accept these three
    substitutions", and a reader can disagree with any of them.
    """
    applied: list[str] = []

    if value is None:
        return None, ()

    if isinstance(value, bool):
        applied.append("bool-int")
        return int(value), tuple(applied)

    if isinstance(value, (decimal.Decimal, float)) and scale is not None:
        d = decimal.Decimal(str(value)).quantize(
            decimal.Decimal(1).scaleb(-scale), rounding=decimal.ROUND_HALF_EVEN)
        applied.append("decimal-scale")
        return d, tuple(applied)

    if isinstance(value, _dt.datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        else:
            dt = dt.astimezone(_dt.timezone.utc)
        if ts_precision == "second":
            dt = dt.replace(microsecond=0)
        applied.append("timestamp-utc")
        return dt, tuple(applied)

    if value == "" and nullable:
        applied.append("null-unification")
        return None, tuple(applied)

    return value, ()
