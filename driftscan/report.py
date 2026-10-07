"""Rendering a drift report, including what it did not check."""

from __future__ import annotations

from .compare import Report

_BAR = "-" * 76


def render(rep: Report, source_name: str = "source", target_name: str = "target") -> str:
    out = ["", _BAR, "DRIFT REPORT  %s -> %s" % (source_name, target_name), _BAR,
           "  rows            %s %d   |   %s %d"
           % (source_name, rep.source_rows, target_name, rep.target_rows),
           "  buckets         %d compared, %d equal, %d unread"
           % (rep.buckets_compared, rep.buckets_equal, len(rep.buckets_unread)),
           "  coverage        %.1f%%" % (rep.coverage * 100)]

    if rep.rules_applied:
        out += ["", "  normalisations applied (each one erases a distinction on purpose):"]
        from .normalize import RULES
        for r in RULES:
            if r.name in rep.rules_applied:
                # Split on ". " not "." - the rationale text contains decimals
                # like "Decimal(18,4) 1.5", and splitting on a bare period cut
                # the sentence off mid-number.
                first = r.why.split(". ")[0].rstrip(".")
                out.append("    - %-18s %s." % (r.name, first))

    out += ["", _BAR]
    if not rep.divergences and rep.complete:
        out += ["VERDICT: IN SYNC",
                "  Every bucket was compared and every bucket matched."]
    elif not rep.divergences and not rep.complete:
        out += ["VERDICT: NOT FULLY CHECKED",
                "  No drift found in what was read, but %d buckets were never opened."
                % len(rep.buckets_unread),
                "  That is not the same as being in sync, and it is not reported as such."]
    else:
        out += ["VERDICT: DRIFTED  (%d rows)" % len(rep.divergences)]
        if not rep.complete:
            out.append("  ...and %d buckets were never opened, so there may be more."
                       % len(rep.buckets_unread))
        out.append("")
        kinds: dict[str, int] = {}
        for d in rep.divergences:
            kinds[d.kind] = kinds.get(d.kind, 0) + 1
        for k, n in sorted(kinds.items()):
            out.append("  %-20s %d" % (k, n))
        out += ["", "  first few:"]
        for d in rep.divergences[:6]:
            detail = (" columns=%s" % (",".join(d.columns),)) if d.columns else ""
            out.append("    %-20s key=%s%s" % (d.kind, d.key, detail))
        if len(rep.divergences) > 6:
            out.append("    ... and %d more" % (len(rep.divergences) - 6))
    out.append(_BAR)
    return "\n".join(out)
