"""A ClickHouse -> Postgres sync that reports success and is wrong anyway.

No database required. The two stores are lists; the point is the comparison
strategy, not the drivers. Swap `source`/`target` for real cursors and nothing
else changes.

The simulated sync does what real ones do when they fail quietly: drops a
handful of rows under a retry, writes one stale value, and lets a type
round-trip mangle another. It then reports success, because it did not crash.
"""

from __future__ import annotations

import datetime as dt
import decimal
import random
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from driftscan.compare import bucket_of, compare
from driftscan.report import render

N = 50_000
COLUMNS = ("id", "account", "amount", "updated_at", "active")
SCHEMA = {"amount": {"scale": 4}, "updated_at": {"ts_precision": "second"},
          "active": {}, "account": {"nullable": True}}


def clickhouse_side(n=N):
    rng = random.Random(7)
    base = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    for i in range(n):
        yield {"id": i,
               "account": "acct-%05d" % rng.randrange(900),
               "amount": decimal.Decimal("%d.%02d" % (rng.randrange(9999), rng.randrange(100))),
               "updated_at": base + dt.timedelta(seconds=i),
               "active": rng.random() < 0.8}


def postgres_side(rows):
    """The same rows after a sync that 'succeeded'."""
    rng = random.Random(11)
    out = []
    for r in rows:
        # Benign round-trips that a naive differ would scream about:
        # bool -> 0/1, Decimal -> padded scale, timestamp -> microsecond precision.
        r = dict(r)
        r["active"] = int(r["active"])
        r["amount"] = decimal.Decimal(str(r["amount"])).quantize(decimal.Decimal("0.0001"))
        r["updated_at"] = r["updated_at"] + dt.timedelta(microseconds=rng.randrange(999999))
        out.append(r)

    # And the three real failures.
    dropped = {out[i]["id"] for i in (1234, 20001, 33333, 49999)}   # lost under a retry
    out = [r for r in out if r["id"] not in dropped]
    out[777]["amount"] = decimal.Decimal("0.0000")                  # stale write
    out[41000]["account"] = None                                    # mangled round-trip
    return out


def main() -> int:
    src = list(clickhouse_side())
    tgt = postgres_side(src)
    print("sync job says: OK, %d rows written" % len(tgt))

    n_buckets = 256
    rep = compare(src, tgt, "id", COLUMNS, SCHEMA, n_buckets=n_buckets)
    print(render(rep, "clickhouse.transactions", "postgres.transactions"))

    # What the bucketing actually saved, measured rather than asserted.
    suspect = n_buckets - rep.buckets_equal
    read = sum(1 for r in src if bucket_of(r["id"], n_buckets) < n_buckets
               and bucket_of(r["id"], n_buckets) in
               {bucket_of(d.key, n_buckets) for d in rep.divergences})
    print("\nRows read individually: %d of %d (%.2f%%). The other %d buckets were"
          % (read, len(src), 100 * read / len(src), rep.buckets_equal))
    print("settled by one count-and-XOR aggregate each, which in a real store is")
    print("a single GROUP BY per side.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
