#!/usr/bin/env python3
"""Estimate occupancy-driven cooling from the weekday/weekend contrast.

WHY THIS EXISTS
---------------
Every other route tried against the cooling floor failed the same way: a load
that runs at a steady level is invisible in aggregate demand, because any
estimator capable of removing seasonal confounds also removes the level. The
site's Tier 1 recovers about a seventh of the ECUK cooling anchor for exactly
this reason, and says so.

This route does not try to see cooling. It looks at the DIFFERENCE between two
populations experiencing identical weather - the same calendar days, the same
hours, the same temperatures, differing only in whether buildings are occupied.
Everything invariant to occupancy cancels: baseload, process refrigeration, the
weather itself. What survives is load that responds to BOTH temperature and
occupancy, and comfort cooling is the obvious candidate.

Crucially the contrast yields a LEVEL rather than a slope, because weekend days
supply the counterfactual rather than an extrapolation to an unobservable zero.

THE METHOD
----------
Within a season and an hour band, bin by outdoor temperature. In each bin take

    gap(T) = mean weekday demand - mean weekend demand

and regress gap on T. A positive slope means the occupancy premium grows with
temperature. Integrating that growth over the observed temperatures gives an
energy.

THE PLACEBO, AND WHY IT MATTERS
-------------------------------
A weekday/weekend gap that grew with temperature for some unrelated reason
would look identical. The test is to repeat the contrast at NIGHT in the same
season: offices are shut on a weekday night and a weekend night alike, so the
occupancy difference is switched off while the weather is unchanged. If the
daytime growth is comfort cooling, the night growth must be near zero.

On the record to August 2026 the day slope is +0.107 GW/degC and the night
slope is -0.065, on 6 and 3 bins - opposite signs, which is the result the
hypothesis predicts and a generic artefact would not produce. The night test is
thin because Britain has few hot nights; it should be rerun as the record
grows. This script FAILS if the night slope is not clearly below the day slope,
rather than reporting a number that has not earned its place.

WHAT THIS DOES AND DOES NOT MEASURE
-----------------------------------
  IS:  the temperature-responsive part of the occupancy premium - cooling that
       runs when buildings are in use and stops when they are not
  NOT: cooling in buildings open at weekends. Retail, hospitality and leisure
       are open and cooled, so they appear on BOTH sides and cancel. This is
       therefore a LOWER BOUND on commercial comfort cooling, and possibly a
       substantial understatement
  NOT: process refrigeration, which runs seven days a week and cancels
  NOT: cooling in dwellings, which have no weekday/weekend occupancy pattern
       of this kind
  NOT: isolated from other occupancy load that happens to track temperature -
       ventilation rates in particular. Cooling should dominate; it is not
       proven to

The day-band figure is measured directly. The whole-day figure scales it by
24/band-hours and is an OVERSTATEMENT, because cooling does not run overnight
at the daytime rate. Report the range, not a point.

USAGE
-----
    python3 analysis/fit_weekend_contrast.py docs/retro.json
    python3 analysis/fit_weekend_contrast.py --selftest

Causeway Energies, 17 August 2026. Offline: not on the daily build path.
"""

import datetime as dt
import json
import math
import random
import sys

SUMMER = (5, 6, 7, 8, 9)
DAY_HOURS = (12, 17)
NIGHT_HOURS = (1, 5)
BINS = [(12, 15), (15, 17), (17, 19), (19, 21), (21, 24), (24, 30)]
MIN_PER_CELL = 20
ANCHOR_GWH = 4780.0        # ECUK services cooling limb, 46% of U5
REF_C = 14.0               # integrate the growth above this


def load(path):
    d = json.load(open(path))
    t0 = dt.datetime.fromisoformat(d["start_day"])
    temp, dem = d["temp_C"], d["demand_GW"]
    out = []
    for i in range(min(len(temp), len(dem))):
        if temp[i] is None or dem[i] is None:
            continue
        out.append((t0 + dt.timedelta(hours=i), temp[i], dem[i]))
    return out


def gap_slope(rows, hours, months=SUMMER, bins=BINS):
    """gap(T) = weekday mean - weekend mean, regressed on T."""
    pts = []
    for lo, hi in bins:
        wd = [g for ts, t, g in rows if ts.month in months
              and hours[0] <= ts.hour <= hours[1]
              and ts.weekday() < 5 and lo <= t < hi]
        we = [g for ts, t, g in rows if ts.month in months
              and hours[0] <= ts.hour <= hours[1]
              and ts.weekday() >= 5 and lo <= t < hi]
        if len(wd) >= MIN_PER_CELL and len(we) >= MIN_PER_CELL:
            pts.append(((lo + hi) / 2.0,
                        sum(wd) / len(wd) - sum(we) / len(we),
                        len(wd), len(we)))
    if len(pts) < 3:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    sl = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
    ss = sum((p[1] - my) ** 2 for p in pts)
    rs = sum((p[1] - (my + sl * (p[0] - mx))) ** 2 for p in pts)
    return {"slope": sl, "r2": (1 - rs / ss) if ss else 0.0,
            "mean_gap": my, "points": pts}


def energy(rows, slope, hours, months=SUMMER, ref=REF_C, days=365):
    """Integrate the growth over the trailing window's own hours."""
    sel = [(ts, t) for ts, t, _ in rows
           if ts.month in months and hours[0] <= ts.hour <= hours[1]
           and ts.weekday() < 5]
    if not sel:
        return 0.0, 0
    cutoff = max(ts for ts, _ in sel) - dt.timedelta(days=days)
    sel = [(ts, t) for ts, t in sel if ts >= cutoff]
    return sum(slope * max(0.0, t - ref) for _, t in sel), len(sel)


def report(rows):
    day = gap_slope(rows, DAY_HOURS)
    night = gap_slope(rows, NIGHT_HOURS)
    if not day:
        print("too few paired cells in the day band to fit")
        return 1
    print("\nDAY %02d-%02dh, summer" % DAY_HOURS)
    print("  slope    %+.4f GW/degC" % day["slope"])
    print("  R2       %6.2f   on %d bins" % (day["r2"], len(day["points"])))
    print("  mean gap %6.2f GW" % day["mean_gap"])
    for p in day["points"]:
        print("    %5.1f C  gap %5.2f  (%3d weekday / %3d weekend hours)" % p)

    print("\nPLACEBO - NIGHT %02d-%02dh, same season" % NIGHT_HOURS)
    if not night:
        print("  NOT ENOUGH DATA to run the placebo. Britain has few hot")
        print("  nights; without it the daytime slope cannot be attributed")
        print("  to cooling. Rerun when the record covers another summer.")
        return 1
    print("  slope    %+.4f GW/degC" % night["slope"])
    print("  R2       %6.2f   on %d bins" % (night["r2"], len(night["points"])))
    print("  mean gap %6.2f GW  <- standing occupancy load, not cooling"
          % night["mean_gap"])
    for p in night["points"]:
        print("    %5.1f C  gap %5.2f  (%3d/%3d)" % p)

    print("\nPLACEBO VERDICT")
    if day["slope"] <= 0:
        print("  FAIL: the day gap does not grow with temperature.")
        return 1
    if night["slope"] >= day["slope"] * 0.5:
        print("  FAIL: the night gap grows nearly as fast as the day gap, so")
        print("  the contrast is picking up something other than occupancy-")
        print("  driven cooling. Do not use this estimate.")
        return 1
    print("  PASS: the gap grows with temperature only when buildings are")
    print("  occupied. Night slope %+.4f against day %+.4f."
          % (night["slope"], day["slope"]))
    if len(night["points"]) < 5:
        print("  CAUTION: only %d night bins over a narrow temperature range."
              % len(night["points"]))
        print("  The placebo is directional, not conclusive.")

    e, n = energy(rows, day["slope"], DAY_HOURS)
    span = DAY_HOURS[1] - DAY_HOURS[0] + 1
    print("\nENERGY, integrating the growth above %.0f C" % REF_C)
    print("  weekday hours in band, trailing year   %6d" % n)
    print("  measured in band                       %6.0f GWh/yr  (%.0f%% of anchor)"
          % (e, 100 * e / ANCHOR_GWH))
    print("  scaled to 24 h - AN UPPER BOUND        %6.0f GWh/yr  (%.0f%%)"
          % (e * 24 / span, 100 * e * 24 / span / ANCHOR_GWH))
    print("\n  Report the RANGE. Cooling does not run overnight at the daytime")
    print("  rate, so the scaled figure overstates; the in-band figure counts")
    print("  only six hours a day, so it understates. And weekend-open,")
    print("  weekend-cooled buildings cancel out of the contrast entirely,")
    print("  which makes both ends a lower bound on commercial cooling.")
    return 0


def selftest():
    """Inject a known occupancy-driven cooling signal and recover it."""
    print("SELF-TEST - recovery of an injected signal, no network needed")
    random.seed(5)
    rows = []
    t0 = dt.datetime(2025, 5, 1)
    TRUE = 0.10          # GW per degC of occupancy-driven cooling
    for h in range(24 * 150):
        ts = t0 + dt.timedelta(hours=h)
        t = 16 + 8 * math.sin(h / 24.0 * 0.26) + random.gauss(0, 3)
        base = 28 + random.gauss(0, 1.2)
        occupied = ts.weekday() < 5 and 12 <= ts.hour <= 17
        g = base + (3.0 + TRUE * max(0.0, t - REF_C) if occupied else 0.0)
        rows.append((ts, t, g))
    r = gap_slope(rows, DAY_HOURS)
    print("  injected %.3f GW/degC -> recovered %+.4f  (R2 %.2f)"
          % (TRUE, r["slope"], r["r2"]))
    flat = []
    for h in range(24 * 150):
        ts = t0 + dt.timedelta(hours=h)
        t = 16 + 8 * math.sin(h / 24.0 * 0.26) + random.gauss(0, 3)
        occupied = ts.weekday() < 5 and 12 <= ts.hour <= 17
        flat.append((ts, t, 28 + random.gauss(0, 1.2) + (3.0 if occupied else 0.0)))
    r2 = gap_slope(flat, DAY_HOURS)
    print("  injected 0.000 (flat premium) -> recovered %+.4f  (R2 %.2f)"
          % (r2["slope"], r2["r2"]))
    print("\n  A flat occupancy premium must return a slope near zero, or the")
    print("  estimator would manufacture cooling from occupancy alone.")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    return report(load(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
