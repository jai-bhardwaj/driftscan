import datetime as dt
import decimal
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from driftscan.compare import bucket_of, compare

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s  %s" % (name, detail))


COLS = ("id", "name", "amt")
BASE = [{"id": i, "name": "n%d" % i, "amt": i} for i in range(500)]

print("agreement")
r = compare(BASE, [dict(x) for x in BASE], "id", COLS)
check("identical tables report no divergence", r.divergences == [])
check("identical tables are complete", r.complete)
check("every bucket equal", r.buckets_equal == r.buckets_total, "%d/%d" % (r.buckets_equal, r.buckets_total))
check("row order does not matter", compare(BASE, list(reversed([dict(x) for x in BASE])),
                                           "id", COLS).divergences == [])

print("detection")
t = [dict(x) for x in BASE]; t[7]["name"] = "X"
r = compare(BASE, t, "id", COLS)
check("one changed value is found", len(r.divergences) == 1 and r.divergences[0].key == 7)
check("the changed COLUMN is named", r.divergences[0].columns == ("name",), str(r.divergences[0].columns))

t = [dict(x) for x in BASE][:-1]
check("a missing target row is found",
      compare(BASE, t, "id", COLS).divergences[0].kind == "missing-in-target")
t = [dict(x) for x in BASE] + [{"id": 10**6, "name": "z", "amt": 0}]
check("an extra target row is found",
      compare(BASE, t, "id", COLS).divergences[0].kind == "missing-in-source")

# The reason count is compared as well as XOR. A row present twice XORs to the
# same value as absent-twice, so XOR alone would call this bucket equal.
dup = [dict(x) for x in BASE] + [dict(BASE[3]), dict(BASE[3])]
r = compare(BASE, dup, "id", COLS)
check("an even-count duplicate is caught by the COUNT, not the XOR",
      r.buckets_equal < r.buckets_total, "buckets_equal=%d" % r.buckets_equal)

print("normalisation prevents FALSE drift")
schema = {"amt": {"scale": 4}, "ts": {"ts_precision": "second"}, "flag": {}}
s = [{"id": 1, "amt": decimal.Decimal("1.5"), "ts": dt.datetime(2026, 1, 1, 0, 0, 0), "flag": True}]
t = [{"id": 1, "amt": decimal.Decimal("1.5000"),
      "ts": dt.datetime(2026, 1, 1, 0, 0, 0, 884000), "flag": 1}]
r = compare(s, t, "id", ("id", "amt", "ts", "flag"), schema)
check("decimal scale, sub-second ts and bool/int are not drift", r.divergences == [], str(r.divergences))
check("the rules that fired are reported",
      {"decimal-scale", "timestamp-utc", "bool-int"} <= r.rules_applied, str(r.rules_applied))

print("normalisation does NOT hide REAL drift")
s = [{"id": 1, "amt": decimal.Decimal("1.5")}]
t = [{"id": 1, "amt": decimal.Decimal("1.6")}]
check("a genuine decimal difference still reports",
      compare(s, t, "id", ("id", "amt"), {"amt": {"scale": 4}}).divergences != [])
s = [{"id": 1, "name": ""}]
t = [{"id": 1, "name": None}]
check("'' vs NULL IS drift when the column is not nullable",
      compare(s, t, "id", ("id", "name"), {"name": {"nullable": False}}).divergences != [])
check("'' vs NULL is NOT drift when it is nullable",
      compare(s, t, "id", ("id", "name"), {"name": {"nullable": True}}).divergences == [])

print("honesty about coverage")
t = [dict(x) for x in BASE]
for i in range(0, 400, 7):
    t[i]["name"] = "X"
r = compare(BASE, t, "id", COLS, max_drilldown=2)
check("a capped scan is NOT reported as complete", not r.complete)
check("the unread buckets are listed", len(r.buckets_unread) > 0, str(len(r.buckets_unread)))
check("coverage is still reported", 0 < r.coverage <= 1.0)

print("bucket stability")
check("bucket_of is stable within a process", bucket_of("k", 64) == bucket_of("k", 64))
out = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0,'.'); from driftscan.compare import bucket_of; print(bucket_of('k',64))"],
    capture_output=True, text=True, cwd=__file__.rsplit("/", 2)[0],
    env={"PYTHONHASHSEED": "12345", "PATH": "/usr/bin:/bin"})
check("bucket_of is stable ACROSS processes with a different PYTHONHASHSEED",
      out.stdout.strip() == str(bucket_of("k", 64)), "child=%r ours=%r" % (out.stdout.strip(), bucket_of("k", 64)))

print("\n%d failed" % len(FAILED) if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
