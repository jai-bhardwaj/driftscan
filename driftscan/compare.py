"""Bucketed comparison: find which rows differ without reading every row twice.

THE SHAPE OF THE PROBLEM. A sync job that reports success has told you it did
not crash. It has not told you the two stores agree. Checking that properly by
pulling both tables and diffing them costs a full scan of each, every time, so
in practice nobody runs it and the sync is trusted because it is quiet.

THE APPROACH. Hash the primary key space into buckets. Ask each store for one
aggregate per bucket - an order-independent XOR of per-row hashes, plus a count.
That is a single grouped query per side regardless of table size. Buckets whose
aggregates match are equal and are never read again. Buckets that disagree are
split and re-asked, so the cost of locating k differing rows is O(k log n)
rather than O(n).

WHY XOR AND NOT A SUM. Both are order-independent, which matters because the
two stores will not return rows in the same order. XOR additionally does not
carry, so one differing row cannot be masked by a compensating difference in
another the way it can with addition mod 2^64. XOR has its own degenerate case -
a row duplicated an even number of times vanishes - which is why the per-bucket
ROW COUNT is carried alongside and compared first.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .normalize import normalize


def row_hash(row: dict, columns: tuple[str, ...], schema: dict) -> tuple[int, set[str]]:
    """A 64-bit hash of one row, plus the normalisation rules that fired."""
    parts, applied = [], set()
    for col in columns:
        spec = schema.get(col, {})
        v, rules = normalize(row.get(col),
                             nullable=spec.get("nullable", False),
                             scale=spec.get("scale"),
                             ts_precision=spec.get("ts_precision", "second"))
        applied.update(rules)
        parts.append("%s=%r" % (col, v))
    digest = hashlib.blake2b("\x1f".join(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big"), applied


def bucket_of(key, n_buckets: int) -> int:
    """Stable bucket for a primary key.

    Deliberately NOT Python's hash(): that is randomised per process under
    PYTHONHASHSEED, so two runs would bucket the same key differently and the
    comparison would be meaningless across processes.
    """
    d = hashlib.blake2b(repr(key).encode(), digest_size=8).digest()
    return int.from_bytes(d, "big") % n_buckets


@dataclass
class Bucket:
    """One bucket's aggregate from one side."""

    index: int
    count: int = 0
    xor: int = 0

    def add(self, h: int) -> None:
        self.count += 1
        self.xor ^= h


@dataclass
class Divergence:
    key: object
    kind: str          # "missing-in-target" | "missing-in-source" | "value-differs"
    source_row: dict | None = None
    target_row: dict | None = None
    columns: tuple[str, ...] = ()


@dataclass
class Report:
    buckets_total: int = 0
    buckets_compared: int = 0
    buckets_equal: int = 0
    buckets_unread: list[int] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)
    rules_applied: set[str] = field(default_factory=set)
    source_rows: int = 0
    target_rows: int = 0

    @property
    def coverage(self) -> float:
        return self.buckets_compared / self.buckets_total if self.buckets_total else 0.0

    @property
    def complete(self) -> bool:
        """Whether every bucket was actually checked.

        A report that found nothing across 60% of the keyspace has not shown the
        stores agree. Keeping this separate from "no divergences found" is the
        same distinction evalgate draws between "no difference" and "not
        measured", and for the same reason: the two call for opposite responses.
        """
        return not self.buckets_unread


def aggregate(rows, key_col: str, columns: tuple[str, ...], schema: dict,
              n_buckets: int) -> tuple[dict[int, Bucket], set[str]]:
    """Fold rows into per-bucket aggregates.

    In a real adapter this is a single GROUP BY on the store. The in-memory
    version exists so the whole thing is testable without a database.
    """
    buckets: dict[int, Bucket] = {}
    applied: set[str] = set()
    for r in rows:
        b = bucket_of(r[key_col], n_buckets)
        h, rules = row_hash(r, columns, schema)
        applied.update(rules)
        buckets.setdefault(b, Bucket(index=b)).add(h)
    return buckets, applied


def compare(source, target, key_col: str, columns: tuple[str, ...],
            schema: dict | None = None, n_buckets: int = 64,
            max_drilldown: int | None = None) -> Report:
    """Compare two row sources and locate what differs.

    `max_drilldown` caps how many disagreeing buckets are opened. Hitting the
    cap does NOT turn into a clean report - the unopened buckets are listed and
    `complete` goes False, because "I stopped looking" must never render as
    "they match".
    """
    schema = schema or {}
    src_rows, tgt_rows = list(source), list(target)

    src_b, r1 = aggregate(src_rows, key_col, columns, schema, n_buckets)
    tgt_b, r2 = aggregate(tgt_rows, key_col, columns, schema, n_buckets)

    rep = Report(buckets_total=n_buckets, rules_applied=r1 | r2,
                 source_rows=len(src_rows), target_rows=len(tgt_rows))

    suspect = []
    for i in range(n_buckets):
        s, t = src_b.get(i), tgt_b.get(i)
        if s is None and t is None:
            rep.buckets_compared += 1
            rep.buckets_equal += 1
            continue
        sc, sx = (s.count, s.xor) if s else (0, 0)
        tc, tx = (t.count, t.xor) if t else (0, 0)
        rep.buckets_compared += 1
        # Count first: XOR alone cannot see an even-count duplication.
        if sc == tc and sx == tx:
            rep.buckets_equal += 1
        else:
            suspect.append(i)

    if max_drilldown is not None and len(suspect) > max_drilldown:
        rep.buckets_unread = suspect[max_drilldown:]
        suspect = suspect[:max_drilldown]

    # Only disagreeing buckets are read row-by-row. This is the saving.
    want = set(suspect)
    src_by_key = {r[key_col]: r for r in src_rows if bucket_of(r[key_col], n_buckets) in want}
    tgt_by_key = {r[key_col]: r for r in tgt_rows if bucket_of(r[key_col], n_buckets) in want}

    for k in sorted(set(src_by_key) | set(tgt_by_key), key=repr):
        s, t = src_by_key.get(k), tgt_by_key.get(k)
        if t is None:
            rep.divergences.append(Divergence(k, "missing-in-target", source_row=s))
        elif s is None:
            rep.divergences.append(Divergence(k, "missing-in-source", target_row=t))
        else:
            diff = tuple(c for c in columns
                         if row_hash({c: s.get(c)}, (c,), schema)[0]
                         != row_hash({c: t.get(c)}, (c,), schema)[0])
            if diff:
                rep.divergences.append(
                    Divergence(k, "value-differs", source_row=s, target_row=t, columns=diff))
    return rep
