"""Append-only daily archive. The record that is never trimmed.

WHY THIS EXISTS
retro.json is a ROLLING 800-day window: trim() cuts days off the front as new
ones arrive. That is correct for the peak and stress work, which wants a fixed
span of hourly detail, and wrong for the regression work, which wants every
summer ever observed.

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
    dates        contiguous ascending ISO days, no gaps, no duplicates
    temp_C       population-weighted daily mean, degC
    demand_GWh   GB daily underlying demand (NESO ND + embedded), GWh
    solar_GWh    embedded solar generation, GWh
    wind_GWh     embedded + transmission wind, GWh
A missing observation is null IN PLACE. The dates array stays contiguous so the
series can be indexed by date arithmetic and a gap is visible rather than
silently closed up by a shorter array.

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

SCHEMA = 1
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "docs", "daily.json")

# Settlement and ND revise for a few days after the fact. Anything older than
# this is frozen: upsert() will not change it without force=True, which exists
# for a declared correction and should leave a note in the commit, not be used
# to paper over a feed that returned nonsense.
REVISE_DAYS = 3

FIELDS = ("temp_C", "demand_GWh", "solar_GWh", "wind_GWh")


class ArchiveError(Exception):
    """Raised when an operation would break the append-only invariant."""


def empty():
    return {"schema": SCHEMA, "updated": None, "dates": [],
            "temp_C": [], "demand_GWh": [], "solar_GWh": [], "wind_GWh": [],
            "sources": {}}


def load(path=PATH):
    try:
        with open(path) as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        return None
    if store.get("schema") != SCHEMA:
        return None
    return store


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


def seed_from_retro(retro_store):
    """Build a daily archive from the hourly rolling store. No network.

    Buckets the hourly arrays to daily means exactly as fit_cooling.daily_from_
    retro does, so a fit on the seeded archive reproduces a fit on retro.json.
    Days with fewer than 20 good hours are dropped, which can leave holes, so
    the result is returned as rows for the caller to inspect rather than
    written straight in.
    """
    T = retro_store["temp_C"]
    D = retro_store["demand_GW"]
    S = retro_store.get("solar_GW") or []
    W = retro_store.get("wind_GW") or []
    t0 = dt.datetime.fromisoformat(retro_store["start_day"])
    buckets = {}
    for i in range(min(len(T), len(D))):
        if T[i] is None or D[i] is None:
            continue
        day = (t0 + dt.timedelta(hours=i)).date()
        buckets.setdefault(day, []).append((
            T[i], D[i],
            (S[i] or 0.0) if i < len(S) else 0.0,
            (W[i] or 0.0) if i < len(W) else 0.0))
    rows = []
    for d_, v in sorted(buckets.items()):
        if len(v) < 20:
            continue
        n = len(v)
        rows.append({"d": d_,
                     "temp_C": round(sum(x[0] for x in v) / n, 3),
                     # GW averaged over the day -> GWh for the day
                     "demand_GWh": round(sum(x[1] for x in v) / n * 24.0, 2),
                     "solar_GWh": round(sum(x[2] for x in v), 2),
                     "wind_GWh": round(sum(x[3] for x in v), 2)})
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
