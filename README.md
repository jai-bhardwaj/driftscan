# driftscan

A sync job that reports success has told you it did not crash. It has not told
you the two stores agree.

`driftscan` checks whether they actually do — without reading both tables in
full, and without reporting a verdict it has not earned.

## The problem

I maintain a ClickHouse → Postgres sync at work. The failure that matters is not
the one that pages you; it is the one where the job completes, logs a row count,
and is quietly wrong. Rows lost under a retry. A stale value written over a fresh
one. A type round-trip that mangles a field.

Checking for that properly means comparing every row on both sides, every time,
which costs a full scan each and so never gets run. The sync is trusted because
it is quiet.

## How it checks

**Bucket the key space and compare aggregates.** Each side returns, per bucket, a
row count and an order-independent XOR of per-row hashes. That is one `GROUP BY`
per side regardless of table size. Buckets whose aggregates agree are equal and
are never read again; only disagreeing buckets are opened row by row.

On the 50,000-row example below, locating six bad rows reads **1,195 of them
individually — 2.39%**. The other 250 buckets are settled by one aggregate each.

**Why XOR and not a sum.** Both are order-independent, which matters because the
two stores will not return rows in the same order. XOR additionally does not
carry, so one differing row cannot be masked by a compensating difference
elsewhere the way it can with addition mod 2⁶⁴. XOR has its own blind spot — a
row duplicated an even number of times vanishes — which is exactly why the
per-bucket **row count is carried alongside and compared first**. There is a test
for that case.

## The part that decides whether it is useful

A drift detector that hashes raw values reports every type round-trip as drift.
After the third false alarm nobody reads the report, which is worse than not
having one. So `driftscan` normalises before hashing — and every normalisation is
**lossy on purpose**, so each one is named and the report says which fired:

| rule | what it erases |
|---|---|
| `decimal-scale` | `1.5` vs `1.5000` — compared as exact decimals, never floats |
| `timestamp-utc` | ClickHouse `DateTime` is second-precision, Postgres `timestamptz` is microsecond; compared at the coarser one |
| `bool-int` | ClickHouse has no bool; it stores `UInt8` 0/1 |
| `null-unification` | `''` vs `NULL` — **only when the column is nullable on both sides** |

That last qualifier is the one that matters. `'' == NULL` is a real difference in
a non-nullable column, and there is a test asserting it still reports.

A normalisation you cannot see is indistinguishable from a bug.

## Three verdicts, not two

```
VERDICT: IN SYNC
  Every bucket was compared and every bucket matched.

VERDICT: NOT FULLY CHECKED
  No drift found in what was read, but 37 buckets were never opened.
  That is not the same as being in sync, and it is not reported as such.

VERDICT: DRIFTED  (6 rows)
```

"I found nothing" and "I stopped looking" are different answers that call for
opposite responses, and most tooling renders them identically. Capping the scan
with `max_drilldown` never produces a clean report — the unopened buckets are
listed and `complete` goes `False`.

## Run it

No database required. The example is a 50,000-row ClickHouse→Postgres sync that
"succeeds" while dropping four rows, writing one stale value and mangling one
field — and which also applies three harmless type round-trips to make sure
those are *not* reported.

```sh
python3 examples/sync_drift.py
```

```
sync job says: OK, 49996 rows written

DRIFT REPORT  clickhouse.transactions -> postgres.transactions
  rows            clickhouse.transactions 50000   |   postgres.transactions 49996
  buckets         256 compared, 250 equal, 0 unread
  coverage        100.0%

VERDICT: DRIFTED  (6 rows)
  missing-in-target    4
  value-differs        2
    value-differs        key=777   columns=amount
    value-differs        key=41003 columns=account

Rows read individually: 1195 of 50000 (2.39%).
```

```python
from driftscan import compare, render

rep = compare(clickhouse_cursor, postgres_cursor, key_col="id",
              columns=("id", "amount", "updated_at"),
              schema={"amount": {"scale": 4}}, n_buckets=256)
print(render(rep))
if not rep.complete or rep.divergences:
    raise SystemExit(1)
```

`compare` takes any two iterables of dicts, so the adapters are whatever your
driver returns.

## Design notes

**`bucket_of` does not use Python's `hash()`.** That is randomised per process
under `PYTHONHASHSEED`, so two runs would bucket the same key differently and the
comparison would be meaningless across processes. It uses BLAKE2b. There is a
test that re-runs it in a subprocess with a different seed.

**No dependencies.** Standard library only.

## Tests

19 assertions.

```sh
python3 tests/test_compare.py
```

The ones worth reading are the pair that pull in opposite directions: that
normalisation suppresses false drift (padded decimals, sub-second timestamps,
bool/int), *and* that it does not hide real drift (a genuine `1.5` vs `1.6`, and
`''` vs `NULL` in a non-nullable column).

## What this is not

- **Not a sync tool.** It tells you the two stores disagree. Repairing them is
  your job, and should be, because the right repair depends on which side you
  trust.
- **Not a schema differ.** It compares rows in columns you name.
- **Not streaming.** It is a point-in-time check. Against a live table, expect
  rows written mid-scan to show as drift.

## Licence

MIT.
