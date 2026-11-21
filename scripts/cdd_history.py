"""Annual GB cooling degree days, as far back as ERA5 reaches.

WHY THIS EXISTS. The cooling panel's tiers are deterministic functions of
one day's weather - tier 1 is k x CDD^2 with a single k, tier 2 is
degree-hours x one per-dwelling response - so a long window of them shows
five summers of WEATHER and cannot show the fleet growing (see build.py,
beside ANNUAL_TWH). The weather, however, is a real series with a real
trend, and it is the only long series this site can honestly draw: ERA5
goes back to 1940 and needs no assumption about install base.

So this publishes the thing the panel can actually evidence over 25 years -
how much cooling demand the CLIMATE has been asking for - and leaves the
question of how much plant exists to answer it where it belongs.

    python3 scripts/cdd_history.py                 # 25 years, prints a table
    python3 scripts/cdd_history.py --years 40
    python3 scripts/cdd_history.py --out docs/cdd_history.json

NETWORK. archive-api.open-meteo.com is refused from Claude's container and
from the desktop bridge, so this has to run where the daily build runs.
Chunked by decade because a single 25-year request for 13 points is large
enough to time out.

WEIGHTING is lifted from fetch_degree_days.py deliberately: the same twelve
GB points at the same population weights, so an annual figure from here is
the sum of the daily figures the rest of the site already uses. If that
file's points or weights change, change them here too - or the two series
will diverge silently, which is the one failure that would make this chart
worse than no chart.
"""

import argparse
import datetime as dt
import json
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from fetch_degree_days import GB_POINTS, CDD_BASES, ARCHIVE_URL  # noqa: E402

# ERA5 via open-meteo starts in 1940; the archive lags real time by ~5 days.
ERA5_FIRST_YEAR = 1940
LAG_DAYS = 6


def _fetch(start, end, retries=4):
    params = {
        "latitude": ",".join(str(p[1]) for p in GB_POINTS),
        "longitude": ",".join(str(p[2]) for p in GB_POINTS),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_mean", "timezone": "UTC",
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=180)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data]
        except Exception as e:                      # noqa: BLE001
            last = e
            wait = 15 * (attempt + 1)
            print("  attempt %d failed (%s); retry in %ds"
                  % (attempt + 1, e, wait))
            time.sleep(wait)
    raise last


def gb_daily_mean(start, end):
    """{date: population-weighted GB daily mean temp}, chunked by decade."""
    total_w = sum(p[3] for p in GB_POINTS)
    series, counts = defaultdict(float), defaultdict(float)
    a = start
    while a <= end:
        b = min(end, dt.date(a.year + 9, 12, 31))
        print("fetching %s .. %s" % (a, b))
        for (name, lat, lon, w), res in zip(GB_POINTS, _fetch(a, b)):
            d = res["daily"]
            for date, t in zip(d["time"], d["temperature_2m_mean"]):
                if t is None:
                    continue
                series[date] += t * w
                counts[date] += w
        a = b + dt.timedelta(days=1)
    # Same completeness rule as fetch_degree_days: a day missing more than a
    # tenth of the weight is dropped rather than averaged over fewer cities.
    return {d: series[d] / counts[d]
            for d in series if counts[d] >= 0.9 * total_w}


def annual(gb_mean, bases):
    """{base: {year: (cdd, n_days)}} - n_days so partial years can be flagged
    rather than silently compared with complete ones."""
    out = {}
    for base in bases:
        per, days = defaultdict(float), defaultdict(int)
        for d, t in gb_mean.items():
            y = int(d[:4])
            per[y] += max(0.0, t - base)
            days[y] += 1
        out[base] = {y: (round(per[y], 1), days[y]) for y in sorted(per)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=25)
    ap.add_argument("--out")
    ap.add_argument("--bases", default=",".join(str(b) for b in CDD_BASES))
    # FOR THE NIGHTLY WORKFLOW. Twenty-four of the twenty-five years in this
    # file cannot change: ERA5 does not revise 2004. Only the current year
    # accrues, and it accrues slowly. Re-fetching a quarter-century of daily
    # temperatures for thirteen points every night would be a few seconds of
    # real work and several minutes of somebody else's bandwidth, so the
    # workflow can call this unconditionally and let it decide.
    ap.add_argument("--if-stale", type=int, metavar="DAYS",
                    help="exit without fetching when --out exists and was "
                         "generated within DAYS days")
    args = ap.parse_args()

    if args.if_stale and args.out:
        try:
            with open(args.out, encoding="utf-8") as f:
                prev = json.load(f)
            gen = dt.date.fromisoformat(prev.get("generated", "1970-01-01"))
            age = (dt.date.today() - gen).days
            if age < args.if_stale:
                print("%s is %d day(s) old (< %d), leaving it alone"
                      % (args.out, age, args.if_stale))
                return 0
            print("%s is %d day(s) old, refreshing" % (args.out, age))
        except (OSError, ValueError, KeyError):
            print("%s missing or unreadable, building it" % args.out)

    bases = [float(b) for b in args.bases.split(",")]
    end = dt.date.today() - dt.timedelta(days=LAG_DAYS)
    first = max(ERA5_FIRST_YEAR, end.year - args.years + 1)
    start = dt.date(first, 1, 1)

    gb = gb_daily_mean(start, end)
    print("\n%d GB days, %s .. %s" % (len(gb), min(gb), max(gb)))
    res = annual(gb, bases)

    hdr = "year " + "".join("  base %-6.1f" % b for b in bases)
    print("\n" + hdr)
    print("-" * len(hdr))
    for y in sorted(res[bases[0]]):
        n = res[bases[0]][y][1]
        row = "%4d " % y + "".join("  %10.1f" % res[b][y][0] for b in bases)
        print(row + ("   (%d days)" % n if n < 360 else ""))

    # A trend is worth printing only where the record can carry one, and the
    # spread matters more than the slope for a sizing argument.
    for b in bases:
        ys = [y for y in res[b] if res[b][y][1] >= 360]
        if len(ys) < 10:
            continue
        v = [res[b][y][0] for y in ys]
        n = len(ys)
        mx, my = sum(ys) / n, sum(v) / n
        den = sum((x - mx) ** 2 for x in ys)
        slope = sum((x - mx) * (t - my) for x, t in zip(ys, v)) / den if den else 0
        lo, hi = min(v), max(v)
        print("\nbase %.1f over %d complete years: mean %.0f, range %.0f-%.0f "
              "(hottest year %.1fx the coolest), trend %+.1f CDD/yr"
              % (b, n, my, lo, hi, (hi / lo if lo else float("inf")), slope))

    if args.out:
        payload = {
            "source": "ERA5 via open-meteo, population-weighted GB "
                      "(12 points, weights as fetch_degree_days.py)",
            "generated": dt.date.today().isoformat(),
            "through": max(gb),
            "bases": {str(b): {str(y): res[b][y][0] for y in res[b]}
                      for b in bases},
            "days_per_year": {str(y): res[bases[0]][y][1]
                              for y in res[bases[0]]},
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
