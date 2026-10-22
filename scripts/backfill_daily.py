"""One-off backfill of the append-only daily archive.

    python3 scripts/backfill_daily.py --probe              # feed retention only
    python3 scripts/backfill_daily.py --seed               # from retro.json
    python3 scripts/backfill_daily.py --years 5            # fetch backwards
    python3 scripts/backfill_daily.py --years 5 --dry-run

ORDER OF OPERATIONS
  1. --probe   asks each feed for a single day at the target start and reports
               what came back. Run this FIRST. Open-Meteo is ERA5 and reaches
               1940; NESO historic demand reaches 2009; neither is in doubt.
               The one to watch is whatever the system feed's own retention
               turns out to be, and finding that out costs one request rather
               than an hour of fetching.
  2. --seed    fills the archive from the 800 days already in retro.json, with
               no network at all. Do this before fetching: it is free, it is
               the span the site currently publishes from, and it means a
               failed backfill leaves you no worse off than today.
  3. --years N walks BACKWARDS from the archive start in monthly blocks,
               prepending each. Interrupted halfway it leaves a shorter but
               valid archive, and re-running continues from wherever it got to.

WHY BACKWARDS AND IN BLOCKS
The whole point of this archive is that it is never silently rewritten. So the
backfill never rebuilds the file from scratch: it prepends contiguous blocks to
the front, each one validated before it is written, and the existing days are
untouched throughout. A block that comes back short is dropped with a message
rather than written with a hole in it.

The temperature and system fetchers are imported from retro.py rather than
reimplemented, so backfilled days use the IDENTICAL population weighting and
underlying-demand basis as the days seeded from retro.json. A different
weighting either side of the join would put a step in the series exactly where
the old and new data meet, which is the one place nobody would think to look.
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daily_archive as da          # noqa: E402
import retro                        # noqa: E402

BLOCK_DAYS = 30


def _daily_rows(start_day, end_day):
    """Fetch one block and bucket it to daily means.

    Returns (rows, note). Rows are only returned for days with at least 20
    good hours, matching seed_from_retro and fit_cooling's own threshold.
    """
    temps = retro.fetch_hourly_temp(start_day, end_day)
    sysd = retro.fetch_hourly_system(start_day, end_day)
    dem = sysd.get("demand_GW") or []
    sol = sysd.get("solar_GW") or []
    wnd = sysd.get("wind_GW") or []
    t0 = dt.datetime.fromisoformat(start_day)
    buckets = {}
    for i in range(len(temps)):
        t = temps[i]
        d = dem[i] if i < len(dem) else None
        if t is None or d is None:
            continue
        day = (t0 + dt.timedelta(hours=i)).date()
        buckets.setdefault(day, []).append((
            t, d,
            (sol[i] or 0.0) if i < len(sol) else 0.0,
            (wnd[i] or 0.0) if i < len(wnd) else 0.0))
    rows, thin = [], 0
    for d_, v in sorted(buckets.items()):
        if len(v) < 20:
            thin += 1
            continue
        n = len(v)
        rows.append({"d": d_,
                     "temp_C": round(sum(x[0] for x in v) / n, 3),
                     "demand_GWh": round(sum(x[1] for x in v) / n * 24.0, 2),
                     "solar_GWh": round(sum(x[2] for x in v), 2),
                     "wind_GWh": round(sum(x[3] for x in v), 2)})
    return rows, ("%d thin days dropped" % thin if thin else "")


def probe(target_start):
    """One day from each feed at the target start date."""
    day = target_start.isoformat()
    print("PROBE at %s - one day from each feed\n" % day)
    ok = True
    try:
        t = retro.fetch_hourly_temp(day, day)
        good = [v for v in t if v is not None]
        print("  temperature   %d hours, %d good, mean %.2f degC"
              % (len(t), len(good),
                 sum(good) / len(good) if good else float("nan")))
    except Exception as e:                                  # noqa: BLE001
        ok = False
        print("  temperature   FAILED: %s" % e)
    try:
        s = retro.fetch_hourly_system(day, day)
        d = [v for v in (s.get("demand_GW") or []) if v is not None]
        print("  system        %d demand hours, mean %.1f GW"
              % (len(d), sum(d) / len(d) if d else float("nan")))
        if not d:
            ok = False
            print("                retention limit: the feed answered but "
                  "returned no demand for this date")
    except Exception as e:                                  # noqa: BLE001
        ok = False
        print("  system        FAILED: %s" % e)
    print("\n  %s" % ("both feeds serve this date - backfill is available"
                      if ok else
                      "at least one feed will not serve this date. Try a "
                      "nearer start (--years 4, then 3) to find the real "
                      "retention edge before committing to a long run."))
    return ok


def seed(dry_run=False):
    store = da.load() or da.empty()
    r = retro.load()
    if not r:
        print("no retro.json to seed from"); return store
    rows = da.seed_from_retro(r)
    if not rows:
        print("retro.json produced no usable days"); return store
    if store["dates"]:
        # Only the days the archive does not already hold, and only forward.
        last = dt.date.fromisoformat(store["dates"][-1])
        rows = [x for x in rows if x["d"] > last]
        if not rows:
            print("archive already covers retro.json"); return store
        store = da.upsert(store, rows)
    else:
        store = da.upsert(store, rows)
    print("seeded %d days from retro.json" % len(rows))
    print("  " + da.describe(store))
    if not dry_run:
        da.save(store)
    return store


def backfill(years, dry_run=False):
    store = da.load() or da.empty()
    if not store["dates"]:
        print("archive is empty - run --seed first so a failed fetch costs "
              "you nothing"); return store
    target = dt.date.today() - dt.timedelta(days=2 + int(round(years * 365.25)))
    start = dt.date.fromisoformat(store["dates"][0])
    if start <= target:
        print("archive already starts %s, at or before the %s target"
              % (start, target)); return store
    print("backfilling %s back to %s (%d days) in %d-day blocks\n"
          % (start, target, (start - target).days, BLOCK_DAYS))
    done = 0
    while start > target:
        b_end = start - dt.timedelta(days=1)
        b_start = max(target, b_end - dt.timedelta(days=BLOCK_DAYS - 1))
        try:
            rows, note = _daily_rows(b_start.isoformat(), b_end.isoformat())
        except Exception as e:                              # noqa: BLE001
            print("  %s..%s  FETCH FAILED: %s" % (b_start, b_end, e))
            print("  stopping here. The archive is valid and shorter; "
                  "re-run to continue from %s." % start)
            break
        want = (b_end - b_start).days + 1
        if len(rows) != want:
            print("  %s..%s  got %d of %d days%s - block dropped, stopping"
                  % (b_start, b_end, len(rows), want,
                     (" (" + note + ")") if note else ""))
            print("  a partial block would put a hole in the date axis. "
                  "Investigate the feed for this period before continuing.")
            break
        store = da.prepend(store, rows)
        done += len(rows)
        start = b_start
        print("  %s..%s  +%d days  (archive now starts %s)"
              % (b_start, b_end, len(rows), store["dates"][0]))
        if not dry_run:
            da.save(store)           # checkpoint every block: an interrupted
                                     # run keeps what it has already paid for
    print("\n%s" % da.describe(store))
    if dry_run:
        print("(dry run - nothing written)")
    return store


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true",
                    help="check feed retention at the target and STOP")
    ap.add_argument("--seed", action="store_true",
                    help="fill from retro.json, no network")
    ap.add_argument("--backfill", action="store_true",
                    help="walk backwards to the target span")
    # TARGET ONLY. --years says how far back to AIM; it does not by itself
    # fetch anything. It used to: '--probe --years 5' ran the probe and then
    # fell straight through into a full backfill, because the old guard read
    # a target as an instruction. On 22 Oct 2026 that quietly ran seven blocks
    # inside a step labelled 'probe', and the workflow then skipped the commit
    # because the mode said probe - so the work was done and discarded.
    ap.add_argument("--years", type=float, default=0.0,
                    help="target span in years for --probe and --backfill")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.probe or a.seed or a.backfill):
        ap.error("nothing to do: pass --probe, --seed or --backfill "
                 "(--years only sets the target)")
    if a.probe:
        yrs = a.years or 5.0
        probe(dt.date.today() - dt.timedelta(days=2 + int(round(yrs * 365.25))))
        if not (a.seed or a.backfill):
            return
    if a.seed:
        seed(a.dry_run)
    if a.backfill:
        backfill(a.years or 5.0, a.dry_run)


if __name__ == "__main__":
    main()
