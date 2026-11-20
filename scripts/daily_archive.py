"""Append-only daily archive. The record that is never trimmed.

WHY THIS EXISTS
retro.json WAS a rolling 800-day window: trim() cut days off the front as new
ones arrived. That was correct for the peak and stress work, which wants a
fixed span of hourly detail, and wrong for the regression work, which wants
every summer ever observed.

Its cap was retired on 20 Nov 2026 (retro.MAX_DAYS = None), so retro.json no
longer discards anything either. This archive is still the record of account:
it is DAILY, so five years costs about 70 KB here against roughly 1.7 MB of
hours there, and it is the file the pinned fits name and are rebuilt from. The
two stores now differ in resolution and purpose rather than in whether they
forget.

The cost of conflating them was found on 22 Oct 2026. UNDEMEANED_FIT was pinned
on 17 Aug 2026 to a cooling limb of 0.6109 at 95% of the ECUK anchor. Re-run on
the window as it stood two months later the same code gave 0.5503 and 84%, and
the 17 Aug fit could not be reproduced AT ALL, because the days it was fitted on
had been trimmed away. A published number whose own input no longer exists is
not a number anyone can check.

So: this store only ever grows. Days are appended; a short trailing window is
revised because settlement data revises; everything older is frozen and upsert()
refuses to touch it. Hourly detail is deliberately NOT kept - the cooling fit
buckets to daily means on its first pass anyway, and dropping the hour makes
five years cost about 70 KB instead of 1.75 MB.

SCHEMA
    dates         contiguous ascending ISO days, no gaps, no duplicates
    temp_C        population-weighted daily mean from the HOURLY feed, degC
    demand_GWh    GB daily underlying demand (NESO ND + embedded), GWh
    solar_GWh     embedded solar generation, GWh
    wind_GWh      embedded + transmission wind, GWh
  added 20 Nov 2026 (schema 2), to close the remaining data losses:
    gb_mean_temp  the DEGREE-DAY temperature series, degC
    gas_GWh       GB LDZ-sum daily gas demand, GWh
    nts_GWh       NTS daily demand actual, GWh
    ci_g_kwh      daily mean grid carbon intensity, gCO2/kWh
    mid_gbp_mwh   daily mean day-ahead wholesale price, GBP/MWh

WHY BOTH temp_C AND gb_mean_temp. They are not the same series and the
difference is not noise: temp_C is bucketed from the hourly temperature feed,
gb_mean_temp comes from fetch_degree_days, and across the 1,038 overlapping
days they differ by up to 0.053 degC (mean 0.023). Every HDD and CDD on the
site is computed from the SECOND one, so storing only the first would leave the
degree days nearly-but-not-exactly reproducible - which is the failure this
archive exists to prevent. Both are kept; neither is derived from the other.

WHAT IS DELIBERATELY NOT HERE. The Ofgem price cap: it is CAP_HISTORY in
build.py, under version control, so archiving it would duplicate a fact the
repo already holds. HDD and CDD: derived from gb_mean_temp and a base, and the
base is a published choice that may be restated, so storing the inputs keeps
old fits rebuildable under either base.

A missing observation is null IN PLACE. The dates array stays contiguous so the
series can be indexed by date arithmetic and a gap is visible rather than
silently closed up by a shorter array. The fields added at schema 2 start null
before their source's own coverage begins, and those nulls are real: they mark
days for which the figure was already lost when the field was added.

USAGE
    import daily_archive as da
    store = da.load() or da.empty()
    store = da.upsert(store, rows)          # rows: [{"d","temp_C",...}]
    da.save(store)

    da.seed_from_retro(retro.load())        # 800 days, no network needed
    da.vintage(store, end="2026-08-15")     # reproduce a past fit exactly
"""

import datetime as dt
import json
import os

SCHEMA = 2
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "docs", "daily.json")

# Fields present at each schema version. Needed because a bump must MIGRATE an
# existing archive, never replace it: build.py does `load() or empty()`, so a
# load() that returns None for a readable file silently swaps the record of
# account for a fresh one and the next save() overwrites it. That is how a
# routine version bump could have destroyed 1,038 days. _migrate() pads instead.
SCHEMA_1_FIELDS = ("temp_C", "demand_GWh", "solar_GWh", "wind_GWh")

# Settlement and ND revise for a few days after the fact. Anything older than
# this is frozen: upsert() will not change it without force=True, which exists
# for a declared correction and should leave a note in the commit, not be used
# to paper over a feed that returned nonsense.
REVISE_DAYS = 3

FIELDS = SCHEMA_1_FIELDS + ("gb_mean_temp", "gas_GWh", "nts_GWh",
                            "ci_g_kwh", "mid_gbp_mwh")


class ArchiveError(Exception):
    """Raised when an operation would break the append-only invariant."""


def empty():
    """A new, empty archive. Built FROM FIELDS, not a hand-written literal.

    The literal version of this listed the four schema-1 fields by name, so
    adding a field meant remembering to add it here too - and forgetting would
    have produced a store that validate() rejects on its first save."""
    store = {"schema": SCHEMA, "updated": None, "dates": [], "sources": {}}
    for f in FIELDS:
        store[f] = []
    return store


def _migrate(store):
    """Bring an older archive forward in place. Never discards observations.

    Pads fields added by a later schema with null for every existing day, which
    is the truthful value: those days predate the field and the figure was not
    captured. Returns the store, or raises for a version this code does not
    know - raising is deliberate. The alternative, returning None, reaches
    build.py's `load() or empty()` and overwrites the archive with a fresh one;
    a hard failure there is non-fatal to the build (the whole archive block is
    wrapped) and leaves the file untouched, which is the outcome to prefer."""
    v = store.get("schema")
    if v == SCHEMA:
        return store
    if v != 1:
        raise ArchiveError(
            "archive at %s has schema %r, which this code does not know how to "
            "migrate (it handles 1 -> %d). Refusing to touch it: returning an "
            "empty archive here would overwrite the record of account."
            % (PATH, v, SCHEMA))
    n = len(store.get("dates") or [])
    added = []
    for f in FIELDS:
        cur = store.get(f)
        if not isinstance(cur, list):
            store[f] = [None] * n
            added.append(f)
        elif len(cur) < n:
            store[f] = list(cur) + [None] * (n - len(cur))
            added.append(f)
    store["schema"] = SCHEMA
    print("daily archive: migrated schema 1 -> %d, %d day(s) kept, padded %s"
          % (SCHEMA, n, ", ".join(added) if added else "nothing"))
    return store


def load(path=PATH):
    """Load and migrate. None means "no archive here", never "reset it".

    A missing or unparseable file returns None, which legitimately means create
    a new one. A readable file at an unexpected schema RAISES - see _migrate."""
    try:
        with open(path) as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        return None
    return _migrate(store)


def save(store, path=PATH):
    validate(store)
    store["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(store, fh, separators=(",", ":"))
    os.replace(tmp, path)          # atomic: a half-written archive is worse
    return path                    # than no archive


def validate(store):
    """Contiguity, ordering and length agreement. Raises on violation."""
    dates = store["dates"]
    for f in FIELDS:
        if len(store[f]) != len(dates):
            raise ArchiveError("%s has %d values against %d dates"
                               % (f, len(store[f]), len(dates)))
    if not dates:
        return store
    prev = dt.date.fromisoformat(dates[0])
    for i, s in enumerate(dates[1:], 1):
        d_ = dt.date.fromisoformat(s)
        if d_ == prev:
            raise ArchiveError("duplicate date %s at index %d" % (s, i))
        if d_ < prev:
            raise ArchiveError("dates go backwards at index %d (%s after %s)"
                               % (i, s, prev))
        if (d_ - prev).days != 1:
            raise ArchiveError("gap in dates: %s follows %s (%d days)"
                               % (s, prev, (d_ - prev).days))
        prev = d_
    return store


def _as_date(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def upsert(store, rows, force=False):
    """Append new days and revise the trailing window. Never deletes.

    rows: iterable of dicts with "d" (date or ISO string) and any of FIELDS.
    Days older than the revision window are frozen; changing one raises unless
    force=True. Rows that would leave a gap raise - backfill earlier days with
    backfill_daily.py, which rebuilds the front of the array properly.
    """
    rows = sorted(({"d": _as_date(r["d"]),
                    **{f: r.get(f) for f in FIELDS}} for r in rows),
                  key=lambda r: r["d"])
    if not rows:
        return store
    if not store["dates"]:
        first = rows[0]["d"]
        store["dates"] = [first.isoformat()]
        for f in FIELDS:
            store[f] = [rows[0].get(f)]
        rows = rows[1:]

    idx = {s: i for i, s in enumerate(store["dates"])}
    last = dt.date.fromisoformat(store["dates"][-1])
    frozen_before = last - dt.timedelta(days=REVISE_DAYS)

    for r in rows:
        d_, key = r["d"], r["d"].isoformat()
        i = idx.get(key)
        if i is not None:
            if d_ <= frozen_before and not force:
                cur = {f: store[f][i] for f in FIELDS}
                new = {f: r.get(f) for f in FIELDS}
                if any(cur[f] != new[f] for f in FIELDS
                       if new[f] is not None):
                    raise ArchiveError(
                        "refusing to revise frozen day %s (older than the "
                        "%d-day revision window). Pass force=True and say why "
                        "in the commit." % (key, REVISE_DAYS))
                continue
            for f in FIELDS:
                if r.get(f) is not None:
                    store[f][i] = r[f]
            continue
        if d_ < dt.date.fromisoformat(store["dates"][0]):
            raise ArchiveError(
                "%s precedes the archive start %s. Use backfill_daily.py to "
                "extend backwards - upsert only appends." % (key,
                                                             store["dates"][0]))
        gap = (d_ - last).days
        if gap > 1:
            raise ArchiveError(
                "appending %s would leave a %d-day hole after %s. Fetch the "
                "missing days first; the archive does not carry gaps in its "
                "date axis." % (key, gap - 1, last.isoformat()))
        store["dates"].append(key)
        for f in FIELDS:
            store[f].append(r.get(f))
        idx[key] = len(store["dates"]) - 1
        last = d_
    return validate(store)


def prepend(store, rows):
    """Extend BACKWARDS. The one operation that writes before the start.

    Used only by the backfill. Requires the new block to end exactly one day
    before the current start, so the result is still contiguous.
    """
    rows = sorted(({"d": _as_date(r["d"]),
                    **{f: r.get(f) for f in FIELDS}} for r in rows),
                  key=lambda r: r["d"])
    if not rows:
        return store
    if not store["dates"]:
        return upsert(store, rows)
    want = dt.date.fromisoformat(store["dates"][0]) - dt.timedelta(days=1)
    if rows[-1]["d"] != want:
        raise ArchiveError(
            "prepend block ends %s but must end %s to stay contiguous with the "
            "existing start %s" % (rows[-1]["d"], want, store["dates"][0]))
    seen = set()
    for r in rows:
        if r["d"] in seen:
            raise ArchiveError("duplicate date %s in prepend block" % r["d"])
        seen.add(r["d"])
    if (rows[-1]["d"] - rows[0]["d"]).days + 1 != len(rows):
        raise ArchiveError("prepend block is not contiguous")
    store["dates"] = [r["d"].isoformat() for r in rows] + store["dates"]
    for f in FIELDS:
        store[f] = [r.get(f) for r in rows] + store[f]
    return validate(store)


def fill_nulls(store, field, by_date, label=""):
    """Populate a field only where it is currently null. Never overwrites.

    The operation the schema-2 backfill needs, and deliberately not upsert().
    upsert() guards frozen days by refusing any CHANGE to them, which is right
    for a revised observation and wrong here: giving a frozen day a value for a
    field that was null is not a revision, it is the field arriving late. But
    force=True would have been the wrong tool too - it can rewrite anything,
    including real observations, which is exactly what this archive exists to
    prevent.

    So the invariant is narrower than either: a non-null value is never touched,
    whatever is offered for it. That makes the operation safe to re-run and
    impossible to use for a restatement by accident. A value that genuinely
    needs correcting goes through upsert(force=True) with a note in the commit,
    where it is visible.

    by_date: {ISO date or date -> value}. Returns a report dict.
    """
    if field not in FIELDS:
        raise ArchiveError("%r is not an archive field" % field)
    norm = {}
    for k, v in by_date.items():
        if v is None:
            continue
        norm[k if isinstance(k, str) else k.isoformat()] = v
    filled = kept = absent = 0
    for i, d_ in enumerate(store["dates"]):
        if store[field][i] is not None:
            if d_ in norm:
                kept += 1          # offered a value, already had one: ignored
            continue
        if d_ in norm:
            store[field][i] = norm[d_]
            filled += 1
        else:
            absent += 1
    validate(store)
    rep = {"field": field, "filled": filled, "already_present": kept,
           "still_null": absent, "source": label}
    # Reports only when it CHANGED something. Every build calls this for each
    # schema-2 field and the usual answer is "nothing to do"; printing five
    # no-op lines a run is how a log stops being read, and the thing worth
    # being loud about here is data arriving or going missing, not a no-op.
    if filled:
        print("daily archive: %s <- %s | filled %d, left %d already present, "
              "%d still null" % (field, label or "source", filled, kept,
                                 absent))
    return rep


def seed_from_retro(retro_store):
    """Build a daily archive from the hourly rolling store. No network.

    Buckets the hourly arrays to daily means exactly as fit_cooling.daily_from_
    retro does, so a fit on the seeded archive reproduces a fit on retro.json.
    Days with fewer than 20 good hours are dropped, which can leave holes, so
    the result is returned as rows for the caller to inspect rather than
    written straight in.

    Also carries ci_g_kwh and mid_gbp_mwh from schema 2 as daily MEANS. The
    mean is the right reduction and not merely the obvious one: build.py's
    fetch_ci_weekly_mean takes an unweighted mean of the week's half-hours from
    the same NESO endpoint retro uses, so a simple daily mean averaged over
    seven equal days reproduces that weekly figure exactly - the one exception
    being the two DST days a year, which hold 23 or 25 hours and therefore carry
    slightly the wrong weight. Worth knowing before pinning a week to it.

    The four schema-1 fields keep their original arithmetic to the digit. They
    have to: these values are re-seeded every run, frozen days are compared
    against what is already stored, and a changed rounding would make upsert()
    raise on every frozen day rather than quietly differ.
    """
    T = retro_store["temp_C"]
    D = retro_store["demand_GW"]
    S = retro_store.get("solar_GW") or []
    W = retro_store.get("wind_GW") or []
    C = retro_store.get("ci_g_kwh") or []
    M = retro_store.get("mid_gbp_mwh") or []
    t0 = dt.datetime.fromisoformat(retro_store["start_day"])
    buckets = {}
    for i in range(min(len(T), len(D))):
        if T[i] is None or D[i] is None:
            continue
        day = (t0 + dt.timedelta(hours=i)).date()
        buckets.setdefault(day, []).append(i)

    def _mean(arr, idxs, nd):
        vals = [arr[i] for i in idxs if i < len(arr) and arr[i] is not None]
        return round(sum(vals) / len(vals), nd) if vals else None

    rows = []
    for d_, idxs in sorted(buckets.items()):
        if len(idxs) < 20:
            continue
        n = len(idxs)
        rows.append({
            "d": d_,
            "temp_C": round(sum(T[i] for i in idxs) / n, 3),
            # GW averaged over the day -> GWh for the day
            "demand_GWh": round(sum(D[i] for i in idxs) / n * 24.0, 2),
            "solar_GWh": round(sum((S[i] or 0.0) if i < len(S) else 0.0
                                   for i in idxs), 2),
            "wind_GWh": round(sum((W[i] or 0.0) if i < len(W) else 0.0
                                  for i in idxs), 2),
            "ci_g_kwh": _mean(C, idxs, 1),
            "mid_gbp_mwh": _mean(M, idxs, 2),
        })
    return rows


def vintage(store, start=None, end=None):
    """A truncated view, for reproducing a fit as it stood on a given day.

    This is what makes a pinned coefficient checkable: pin the window with the
    number, and anyone can cut the archive back to it and re-run.
    """
    dates = store["dates"]
    lo = 0 if start is None else next(
        (i for i, s in enumerate(dates) if s >= str(start)[:10]), len(dates))
    hi = len(dates) if end is None else next(
        (i for i, s in enumerate(dates) if s > str(end)[:10]), len(dates))
    out = {"schema": SCHEMA, "updated": store.get("updated"),
           "dates": dates[lo:hi], "sources": store.get("sources", {})}
    for f in FIELDS:
        out[f] = store[f][lo:hi]
    return out


def summers(store):
    """Count complete Jun-Sep seasons. The scarce resource for the cooling fit.

    Returns [(year, n_days, complete)]. A season needs 122 days to be complete,
    and the cooling limb is identified by how many of these the record holds -
    two is not enough to separate the balance point from the slope.
    """
    seen = {}
    for s in store["dates"]:
        d_ = dt.date.fromisoformat(s)
        if 6 <= d_.month <= 9:
            seen[d_.year] = seen.get(d_.year, 0) + 1
    return [(y, n, n >= 122) for y, n in sorted(seen.items())]


def describe(store):
    if not store["dates"]:
        return "daily archive: empty"
    sm = summers(store)
    full = sum(1 for _, _, c in sm if c)
    miss = {f: sum(1 for v in store[f] if v is None) for f in FIELDS}
    return ("daily archive: %s to %s, %d days, %d complete summers (%s)%s"
            % (store["dates"][0], store["dates"][-1], len(store["dates"]),
               full, ", ".join("%d:%d" % (y, n) for y, n, _ in sm),
               "" if not any(miss.values())
               else "; missing " + ", ".join("%s=%d" % (k, v)
                                             for k, v in miss.items() if v)))


if __name__ == "__main__":
    s = load()
    print(describe(s) if s else "daily archive: not created yet (%s)" % PATH)
