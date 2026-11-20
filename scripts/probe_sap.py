"""Does the gas SAP feed reach back five years?

Panel 2's gas line is the one the other three routes are measured against.
If fetch_gas_sap_series cannot serve the backfilled span, the 48- and
60-month windows show three electric routes against nothing, which is worse
than not offering the window.

This could not be answered from the build container - National Gas is not on
its egress allowlist - so it is a standalone probe to run where the build
runs.

    python3 scripts/probe_sap.py            # the full 1,826-day span
    python3 scripts/probe_sap.py 400 800    # specific spans

Prints, per span: how many days came back, the first and last date, the
largest internal gap, and a verdict. A span that returns FEWER days than it
asked for is not a failure by itself - the feed drops non-publication days -
so the gap column is what matters: a multi-month hole mid-series means the
line will break, a scatter of single days will not.
"""

import datetime as dt
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))

from fetch_gas import fetch_gas_sap_series          # noqa: E402

DEFAULT_SPANS = (400, 800, 1826)


def probe(days):
    label = "%d days" % days
    try:
        s = fetch_gas_sap_series(days=days)
    except Exception as e:                           # noqa: BLE001
        print("%-10s UNREACHABLE  %s: %s" % (label, type(e).__name__,
                                             str(e)[:90]))
        return None
    if not s or not s.get("dates"):
        print("%-10s EMPTY        feed answered with no rows" % label)
        return None

    ds = sorted(s["dates"])
    want_from = (dt.date.today() - dt.timedelta(days=days + 1)).isoformat()
    first, last = ds[0], ds[-1]
    gap, gap_at = 0, None
    for a, b in zip(ds, ds[1:]):
        g = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
        if g > gap:
            gap, gap_at = g, a
    short = (dt.date.fromisoformat(first)
             - dt.date.fromisoformat(want_from)).days

    print("%-10s %5d days  %s -> %s  | starts %+d d vs request | "
          "largest gap %d d%s"
          % (label, len(ds), first, last, short, gap,
             (" after " + gap_at) if gap_at else ""))
    return {"n": len(ds), "first": first, "last": last,
            "short": short, "gap": gap}


def main():
    spans = [int(a) for a in sys.argv[1:]] or list(DEFAULT_SPANS)
    print("gas SAP reach probe  (today %s)\n" % dt.date.today().isoformat())
    results = {d: probe(d) for d in spans}

    deep = max(spans)
    r = results.get(deep)
    print()
    if not r:
        print("VERDICT: the deep span did not return. Panel 2's long windows "
              "would have no gas line. Either keep panel 2 at 24 months or "
              "source the earlier SAP days another way.")
        return 1
    # A line is drawn through missing days as a break; a season-long hole is
    # what makes the chart misleading rather than merely sparse.
    if r["gap"] > 31:
        print("VERDICT: reaches %s but with a %d-day hole after %s. The gas "
              "line will break across it. Decide whether a broken reference "
              "line is better than no long window."
              % (r["first"], r["gap"], r["first"]))
        return 2
    if r["short"] > 120:
        print("VERDICT: the feed starts %d days later than asked (%s). The "
              "long windows will show gas over part of their span only."
              % (r["short"], r["first"]))
        return 2
    print("VERDICT: usable. Gas covers %s to %s with no gap over a month, so "
          "the 48- and 60-month windows keep their reference line."
          % (r["first"], r["last"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
