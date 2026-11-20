"""One-off backfill of the append-only daily archive.

    python3 scripts/backfill_daily.py --probe              # feed retention only
    python3 scripts/backfill_daily.py --seed               # from retro.json
    python3 scripts/backfill_daily.py --enrich             # schema-2, offline
    python3 scripts/backfill_daily.py --fill-gaps          # schema-2, fetched
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
  3. --enrich  fills the fields added at schema 2 - gb_mean_temp, gas_GWh,
               nts_GWh, ci_g_kwh, mid_gbp_mwh - on days the archive already
               holds, entirely from retro.json and data.json. No network, so
               like --seed it cannot fail halfway. Run it after a schema
               migration; re-running is a no-op.
  4. --fill-gaps fetches what --enrich could not reach: CI and price before
               retro.json starts, gas before the 770-day request span. Needs
               network, and specifically needs an environment allowed to reach
               api.carbonintensity.org.uk and data.elexon.co.uk - some egress
               policies refuse both with a 403 at the proxy.
  5. --years N walks BACKWARDS from the archive start in monthly blocks,
               prepending each. Interrupted halfway it leaves a shorter but
               valid archive, and re-running continues from wherever it got to.

--enrich/--fill-gaps versus --seed/--years. The first two fill FIELDS on days
that already exist; the last two add DAYS. They use different primitives for a
reason: adding days goes through upsert/prepend, which guard the frozen window
by refusing to change it, while filling a late-arriving field goes through
fill_nulls, which can only populate a null and so cannot restate anything.

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
import json
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


def _neso_catalogue(years, limit=60):
    """Print the NESO demand catalogue and test each year-file. Read-only.

    Added 20 Nov 2026, after probes mapped a hard boundary at 1 Jan 2024:
    2024-01-12 served, 2023-12-17 did not, nor 2022-11-18 or 2021-11-18. A
    retention edge does not fall on a calendar year boundary, so the limit is
    this code's reach, not the feed's. fetch_hourly_system discovers resources
    from ONE slug, 'historic-demand-data', and matches a year by substring on
    the resource name; if the older years are published as their own datasets
    it matches nothing, sends no query, and returns an empty series - which
    reads identically to a feed refusing the date.

    Three causes fit an empty result and need different fixes: the resource is
    not datastore_active (SQL cannot reach it, CSV can), the year lives under
    another slug, or the name has no bare year in it. This distinguishes them
    by printing what the catalogue actually holds, so the fix follows from a
    fact instead of a guess. It writes nothing and commits nothing."""
    import urllib.request
    import urllib.parse

    def _get(url):
        with urllib.request.urlopen(url, timeout=90) as r:
            return json.load(r)

    base = "https://api.neso.energy/api/3/action/"
    print("\n  --- NESO catalogue diagnostic ---")

    # Search first: it finds slugs we have not guessed, which is the whole
    # question if the older years are separate datasets.
    try:
        q = _get(base + "package_search?rows=%d&q=%s"
                 % (limit, urllib.parse.quote("historic demand data")))
        hits = q["result"]["results"]
        print("  package_search 'historic demand data': %d dataset(s)"
              % q["result"]["count"])
        for p in hits:
            print("    %-52s (%d resources)"
                  % (p.get("name", "?")[:52], len(p.get("resources") or [])))
    except Exception as e:                                  # noqa: BLE001
        print("  package_search FAILED: %s: %s" % (type(e).__name__,
                                                   str(e)[:100]))

    # Then the slug the fetcher actually uses, resource by resource, with the
    # flag that decides whether datastore_search_sql can see it at all.
    for slug in ["historic-demand-data"] + [
            "historic-demand-data-%d" % y for y in sorted(years)]:
        try:
            pkg = _get(base + "package_show?id=" + slug)
        except Exception as e:                              # noqa: BLE001
            print("  %-34s package_show: %s" % (slug, str(e)[:44]))
            continue
        rs = pkg["result"].get("resources") or []
        print("  %-34s %d resource(s)" % (slug, len(rs)))
        matched = []
        for r in rs:
            nm = (r.get("name") or "?")
            act = r.get("datastore_active")
            hit = any(str(y) in nm for y in years)
            print("      %-44s active=%-5s year-match=%s"
                  % (nm[:44], act, hit))
            if hit and act and r.get("id"):
                matched.append((nm, r["id"]))

        # THE ROW PROBE, and the reason the first diagnostic settled nothing.
        # The 20 Nov catalogue showed 'Historic Demand Data 2022' present,
        # active AND year-matched - so the resolver found it, queried it, and
        # got zero rows. That eliminates every structural explanation and
        # leaves the predicate: fetch_hourly_system compares SETTLEMENT_DATE
        # as a STRING ('>= 2022-11-18'), which matches nothing if the older
        # files store that column in another format or as a timestamp.
        #
        # So read one row with NO WHERE clause and print the column names and
        # the raw SETTLEMENT_DATE exactly as stored. An unfiltered row cannot
        # come back empty unless the table really is empty, which separates
        # "no data" from "my predicate does not match this format" - the thing
        # the catalogue alone could not tell us.
        for nm, rid in matched:
            try:
                sql = 'SELECT * FROM "%s" LIMIT 1' % rid
                got = _get(base + "datastore_search_sql?sql="
                           + urllib.parse.quote(sql))
                recs = got["result"].get("records") or []
                if not recs:
                    print("      %-30s LIMIT 1 -> 0 rows (table is empty)"
                          % nm[:30])
                    continue
                rec = recs[0]
                print("      %-30s columns: %s"
                      % (nm[:30], ", ".join(sorted(rec.keys()))[:120]))
                for k in sorted(rec):
                    if "DATE" in k.upper() or "SETTLEMENT" in k.upper():
                        print("      %-30s   %s = %r (%s)"
                              % ("", k, rec[k], type(rec[k]).__name__))
            except Exception as e:                          # noqa: BLE001
                print("      %-30s LIMIT 1 FAILED: %s: %s"
                      % (nm[:30], type(e).__name__, str(e)[:70]))
    print("  --- end diagnostic ---")


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
    # Three outcomes, not two, and they need different conclusions. The first
    # version of this collapsed them and then asserted the series "returned
    # NOTHING" even on a 403 - the same imprecision this probe exists to avoid.
    #   unreachable  transport failed. Says nothing about the date. URLError
    #                and HTTPError both subclass OSError, so one clause gets
    #                them, and a blocked egress policy lands here too.
    #   empty        the feed ANSWERED and held no data. _fill raises
    #                RuntimeError("hourly series N/N missing") when more than
    #                5% is absent, which for a one-day probe means all of it.
    #                This is the interesting case and the one worth diagnosing.
    #   ok           served.
    sys_state = "ok"
    try:
        s = retro.fetch_hourly_system(day, day)
        d = [v for v in (s.get("demand_GW") or []) if v is not None]
        print("  system        %d demand hours, mean %.1f GW"
              % (len(d), sum(d) / len(d) if d else float("nan")))
        if not d:
            ok, sys_state = False, "empty"
            print("                the feed answered and returned no demand "
                  "for this date")
    except OSError as e:
        ok, sys_state = False, "unreachable"
        print("  system        UNREACHABLE: %s" % e)
    except Exception as e:                                  # noqa: BLE001
        ok, sys_state = False, "empty" if "missing" in str(e) else "unknown"
        print("  system        FAILED: %s" % e)
    # THE OTHER THREE FEEDS. Added 20 Nov 2026, because the probe tested the
    # two feeds --backfill uses and that is not the question being asked. A
    # backfilled day carries nine fields, and the WEEKLY DERIVATION NEEDS GAS:
    # without it there is no heat split, so no history week can be built for
    # that day however much demand and temperature we have. Probing demand
    # alone can green-light a five-hour walk that cannot produce a single week.
    #
    # These are the feeds --fill-gaps uses afterwards. Each is one day, so the
    # whole probe stays seconds. Nothing here is fatal: a feed that refuses
    # 2022 means those fields stay null for backfilled days, which is honest
    # and visible, not a broken archive.
    extra = {}
    for label, fn in (
            ("carbon int.", lambda: retro.fetch_hourly_ci(day, day)),
            ("wholesale", lambda: retro.fetch_hourly_mid(day, day))):
        try:
            v = fn()
            good = [x for x in (v or []) if x is not None]
            extra[label] = "served" if good else "empty"
            print("  %-13s %d hours, %d good" % (label, len(v or []), len(good)))
        except OSError as e:
            extra[label] = "unreachable"
            print("  %-13s UNREACHABLE: %s" % (label, str(e)[:60]))
        except Exception as e:                              # noqa: BLE001
            extra[label] = "empty" if "missing" in str(e) else "unknown"
            print("  %-13s FAILED: %s" % (label, str(e)[:60]))

    # Gas via _post_gasday, which takes explicit dates - fetch_gas_demand
    # counts back from TODAY, so probing 2022 through it would pull the whole
    # 1,400-day span and stop being a probe.
    try:
        import fetch_gas
        cat = fetch_gas._catalogue()
        by_name = {n: p for p, n in cat}
        ids = [by_name[n] for n in fetch_gas.NTS_PREFERRED_NAMES
               if n in by_name][:1]
        ids += [p for p, n in cat if fetch_gas.LDZ_PATTERN.match(n)][:2]
        if not ids:
            extra["gas"] = "unknown"
            print("  %-13s no publications matched in the catalogue" % "gas")
        else:
            recs = fetch_gas._post_gasday(ids, target_start, target_start)
            n = len(recs or [])
            extra["gas"] = "served" if n else "empty"
            print("  %-13s %d record(s) from %d publication(s)"
                  % ("gas", n, len(ids)))
    except OSError as e:
        extra["gas"] = "unreachable"
        print("  %-13s UNREACHABLE: %s" % ("gas", str(e)[:60]))
    except Exception as e:                                  # noqa: BLE001
        extra["gas"] = "unknown"
        print("  %-13s FAILED: %s: %s" % ("gas", type(e).__name__,
                                          str(e)[:60]))

    # One verdict per outcome. The old wording - "will not serve this date" -
    # claimed more than the probe can know, and on 20 Nov 2026 it sent us
    # looking for a retention edge that was never there.
    verdict = {
        "ok": "both feeds serve this date - backfill is available",
        "unreachable":
            "the system feed could not be REACHED, which says nothing about "
            "whether it holds this date. Check egress and retry; do not read "
            "this as a retention limit.",
        "empty":
            "the feed ANSWERED and held no data for this date. That is either "
            "its retention edge or this code's reach. A limit that falls on 1 "
            "January is the year-file, not retention - catalogue below.",
        "unknown":
            "the system leg failed in a way this probe does not classify. "
            "Catalogue below; read the error above first.",
    }[sys_state if not ok else "ok"]
    print("\n  %s" % verdict)

    # What a backfill to this date would actually YIELD. The point of saying it
    # out loud: demand and temperature get the DAYS into the archive, gas is
    # what makes a history week possible, and CI and price only affect two of
    # the four charted series. Those are different answers to "is five years
    # worth it" and the per-feed lines above do not add themselves up.
    if ok:
        gas = extra.get("gas")
        if gas == "served":
            print("  a backfill to here can carry days AND support weekly "
                  "history: demand, temperature and gas all serve.")
        else:
            print("  CAUTION: gas is '%s' at this date, so days can be added "
                  "but NO weekly history can be built for them - the heat "
                  "split needs gas. Backfilled days would carry weather and "
                  "demand only." % gas)
        thin = [k for k, v in extra.items() if v != "served"]
        if thin:
            print("  fields that would stay null here: %s" % ", ".join(thin))
        print("  and the bill series stops at the 2024-04-01 cap floor "
              "regardless - CAP_HISTORY has no pre-EPG convention.")
    # Only when the feed answered. Diagnosing an unreachable host just adds
    # four more doomed calls and buries the real error.
    if sys_state in ("empty", "unknown"):
        try:
            _neso_catalogue({target_start.year, target_start.year + 1})
        except Exception as e:                              # noqa: BLE001
            # Never let the diagnostic mask the finding it explains.
            print("  diagnostic unavailable: %s: %s"
                  % (type(e).__name__, str(e)[:100]))
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


def enrich(dry_run=False):
    """Fill the schema-2 fields on days the archive already holds. No network.

    Added 20 Nov 2026 with the fields themselves. Separate from --seed because
    --seed only ever appends days AFTER the archive's end (so it cannot reach a
    frozen day, by design), and these fields need populating across days the
    archive has held for months.

    Everything comes from files already in the repo, which is why this needs no
    network and cannot half-fail:
      ci_g_kwh, mid_gbp_mwh   retro.json's hourly arrays, bucketed to daily
      gb_mean_temp            data.json _dd, the degree-day temperature series
      gas_GWh, nts_GWh        data.json _gas, LDZ sum and NTS demand actual

    Uses fill_nulls, so a day that already has a value keeps it and re-running
    is a no-op. Days before a source's own coverage stay null, and that null is
    the truth: the figure was already gone when the field was added."""
    store = da.load() or da.empty()
    if not store["dates"]:
        print("archive is empty - run --seed first"); return store
    reps = []

    r = retro.load()
    if r:
        rows = da.seed_from_retro(r)
        for f in ("ci_g_kwh", "mid_gbp_mwh"):
            reps.append(da.fill_nulls(
                store, f, {x["d"]: x.get(f) for x in rows}, "retro.json"))
    else:
        print("no retro.json - ci_g_kwh and mid_gbp_mwh left as they are")

    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "docs", "data.json")
    try:
        with open(dpath) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        print("no readable data.json - _dd and _gas fields left as they are")
        d = {}

    dd = d.get("_dd") or {}
    if dd.get("dates") and dd.get("gb_mean_temp"):
        reps.append(da.fill_nulls(
            store, "gb_mean_temp",
            dict(zip(dd["dates"], dd["gb_mean_temp"])), "data.json _dd"))

    gas = d.get("_gas") or {}
    for f, key in (("gas_GWh", "ldz_sum"), ("nts_GWh", "nts_demand_actual")):
        src = gas.get(key)
        if isinstance(src, dict) and src:
            reps.append(da.fill_nulls(store, f, src,
                                      "data.json _gas/%s" % key))

    print("  " + da.describe(store))
    if not dry_run:
        da.save(store)
    return store


def fill_gaps(dry_run=False):
    """Fetch the schema-2 fields for days --enrich left null. NEEDS NETWORK.

    --enrich can only reach as far back as the repo's own files: retro.json
    starts 2024-09-09 and the gas feed was only ever REQUESTED for 770 days, so
    after enriching, ci_g_kwh and mid_gbp_mwh are null before 2024-09-09 and
    gas before 2024-10-10. None of that is a feed limit - NESO's CI archive
    reaches 2018, and the gas gap exists because of the span we asked for, not
    the span available. This mode asks for the rest.

    Run it where the feeds are reachable. It cannot be run from every
    environment: both api.carbonintensity.org.uk and data.elexon.co.uk are
    refused outright by some egress policies, which is a 403 at the proxy and
    not a transient error worth retrying. The site's own build runner reaches
    them every run, so that is where this belongs.

    Safe to interrupt and safe to re-run: every write goes through fill_nulls,
    so a day that already has a value is never touched and a half-finished run
    simply leaves fewer nulls than it found."""
    store = da.load() or da.empty()
    if not store["dates"]:
        print("archive is empty - run --seed first"); return store

    def _nulls(field):
        return [d for d, v in zip(store["dates"], store[field]) if v is None]

    # Hourly feeds. Both return a FLAT hourly list starting at start_day 00:00,
    # so the day of index i is start + i//24. Bucketed with the same rule as
    # seed_from_retro - mean over available hours, at least 20 of them - so a
    # fetched day and a seeded day are computed identically and the join does
    # not put a step in the series.
    for field, fetcher, nd in (("ci_g_kwh", retro.fetch_hourly_ci, 1),
                               ("mid_gbp_mwh", retro.fetch_hourly_mid, 2)):
        miss = _nulls(field)
        if not miss:
            print("%s: no gaps" % field); continue
        lo, hi = miss[0], miss[-1]
        print("%s: %d null day(s), fetching %s..%s" % (field, len(miss), lo, hi))
        try:
            hourly = fetcher(lo, hi)
        except Exception as e:
            print("  FAIL (%s: %s) - %s left as it was"
                  % (type(e).__name__, str(e)[:120], field))
            continue
        d0 = dt.date.fromisoformat(lo)
        buckets = {}
        for i, v in enumerate(hourly):
            if v is None:
                continue
            buckets.setdefault(d0 + dt.timedelta(days=i // 24), []).append(v)
        daily = {d_: round(sum(v) / len(v), nd)
                 for d_, v in buckets.items() if len(v) >= 20}
        da.fill_nulls(store, field, daily, "fetched %s..%s" % (lo, hi))

    # Gas. One request reaching past the oldest null; the fetcher counts back
    # from today, so the span is derived from the gap rather than guessed.
    miss = _nulls("gas_GWh") or _nulls("nts_GWh")
    if not miss:
        print("gas_GWh/nts_GWh: no gaps")
    else:
        back = (dt.date.today() - dt.date.fromisoformat(miss[0])).days + 5
        print("gas: %d null day(s) from %s, requesting %d days"
              % (len(miss), miss[0], back))
        try:
            import fetch_gas
            gas = fetch_gas.fetch_gas_demand(days=back)
            for f, key in (("gas_GWh", "ldz_sum"),
                           ("nts_GWh", "nts_demand_actual")):
                src = gas.get(key)
                if isinstance(src, dict) and src:
                    da.fill_nulls(store, f, src,
                                  "fetched %d days / %s" % (back, key))
                else:
                    print("  %s: feed returned nothing usable" % f)
        except Exception as e:
            print("  FAIL (%s: %s) - gas left as it was"
                  % (type(e).__name__, str(e)[:120]))

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
    ap.add_argument("--enrich", action="store_true",
                    help="fill schema-2 fields on existing days, no network")
    ap.add_argument("--fill-gaps", action="store_true", dest="fill_gaps",
                    help="fetch schema-2 fields --enrich could not reach")
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
    if not (a.probe or a.seed or a.enrich or a.fill_gaps or a.backfill):
        ap.error("nothing to do: pass --probe, --seed, --enrich, --fill-gaps "
                 "or --backfill (--years only sets the target)")
    if a.probe:
        yrs = a.years or 5.0
        probe(dt.date.today() - dt.timedelta(days=2 + int(round(yrs * 365.25))))
        if not (a.seed or a.enrich or a.fill_gaps or a.backfill):
            return
    if a.seed:
        seed(a.dry_run)
    if a.enrich:
        enrich(a.dry_run)
    if a.fill_gaps:
        fill_gaps(a.dry_run)
    if a.backfill:
        backfill(a.years or 5.0, a.dry_run)


if __name__ == "__main__":
    main()
