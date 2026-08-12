"""Fetch daily GB gas demand from the National Gas Transmission REST API.

Two things fetched daily:
- Total NTS actual demand (context/display)
- Sum of all LDZ actual offtakes (NDM + DM, all zones) = gas to buildings,
  the regression target; excludes directly-connected power stations, so the
  temperature slope is not contaminated by CCGT dispatch.

LDZ publications are self-discovered from the catalogue. Requests are chunked
in BOTH dimensions to respect the ~3,600-record cap: records returned are
publications x days, so a chunk of 8 publications over 770 days is 6,160 and
the API answers 400. Found on the first 24-month run, 10 Aug 2026 - the
publication chunking had been in place from the start, the date chunking had
not, and it only bit once the window went past ~450 days.
Units auto-detected (kWh / GWh / mcm).
"""

import datetime as dt
import json
import re
import time

import requests

BASE = "https://api.nationalgas.com/operationaldata/v1"
CATALOGUE_URL = f"{BASE}/publications/catalogue"
GASDAY_URL = f"{BASE}/publications/gasday"

NTS_PREFERRED_NAMES = [
    "Demand Actual, NTS, D+1 (Energy)",
    "Demand Actual, NTS, D+1",
    "Demand Actual, NTS, D+6",
]
LDZ_PATTERN = re.compile(
    r"^Demand, Actual (NDM|DM), LDZ\(([A-Z]{2})\), D\+1$")

MCM_TO_GWH = 11.056
CHUNK = 8            # publications per request
MAX_RECORDS = 3500   # API cap is ~3,600 records per call; leave margin
DAY_CHUNK = max(1, MAX_RECORDS // CHUNK)   # days per request, 437


def _harvest(node, found):
    if isinstance(node, dict):
        pid = node.get("publicationId") or node.get("publicationObjectId") \
              or node.get("pubObjId") or node.get("id")
        name = node.get("publicationName") or node.get("publicationObjectName") \
               or node.get("name") or node.get("title")
        if pid and name and str(pid).upper().startswith("PUB"):
            found.append((str(pid), str(name)))
        for v in node.values():
            _harvest(v, found)
    elif isinstance(node, list):
        for v in node:
            _harvest(v, found)


def _autoscale(values_by_date, label):
    vals = sorted(values_by_date.values())
    if not vals:
        raise RuntimeError(f"{label}: no records returned")
    median = vals[len(vals) // 2]
    if median > 1e8:
        factor, unit = 1e-6, "kWh -> GWh"
    elif median > 1000:
        factor, unit = 1.0, "GWh"
    else:
        factor, unit = MCM_TO_GWH, "mcm -> GWh"
    print(f"{label}: median raw {median:.1f} treated as {unit}; "
          f"{len(vals)} days, {len(set(vals))} distinct")
    return {d: round(v * factor, 1) for d, v in values_by_date.items()}


_CATALOGUE = None


def _catalogue():
    """The publication catalogue, fetched at most once per run.

    Two GETs to this endpoint in one run is not reliable: the demand path
    fetched it and got the full list, the SAP call asked again moments later
    and got zero publications. Whether that is rate limiting or a cache miss
    at their end does not matter - one fetch, shared, removes the question.
    A retry is kept because the single remaining call is now load-bearing.
    """
    global _CATALOGUE
    if _CATALOGUE is not None:
        return _CATALOGUE
    last = None
    for attempt in range(3):
        try:
            r = requests.get(CATALOGUE_URL, timeout=60)
            r.raise_for_status()
            found = []
            _harvest(r.json(), found)
            found = sorted(set(found))
            if found:
                _CATALOGUE = found
                return _CATALOGUE
            last = RuntimeError("catalogue returned 0 publications")
        except Exception as e:
            last = e
        time.sleep(5 * (attempt + 1))
    raise last or RuntimeError("catalogue unavailable")


def _post_gasday(pub_ids, start, end):
    r = requests.post(GASDAY_URL, json={
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
        "publicationIds": pub_ids, "latestValue": "Y",
    }, headers={"Content-Type": "application/json"}, timeout=120)
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, list) else payload.get("data", [])


def fetch_gas_demand(days=400):
    catalogue = _catalogue()
    by_name = {name: pid for pid, name in catalogue}

    nts_id = nts_name = None
    for name in NTS_PREFERRED_NAMES:
        if name in by_name:
            nts_id, nts_name = by_name[name], name
            break
    if not nts_id:
        raise RuntimeError("No preferred NTS publication in catalogue")

    ldz_pubs, zones = [], set()
    for pid, name in catalogue:
        m = LDZ_PATTERN.match(name)
        if m:
            ldz_pubs.append((pid, name))
            zones.add(m.group(2))
    print(f"NTS: {nts_id} | {nts_name}")
    print(f"LDZ publications: {len(ldz_pubs)} across zones {sorted(zones)}")
    if len(zones) < 10:
        print("WARNING: fewer LDZ zones than expected (13); "
              "sum may understate buildings demand")

    end = dt.date.today()
    start = end - dt.timedelta(days=days)

    all_ids = [nts_id] + [pid for pid, _ in ldz_pubs]

    # Date windows, so publications x days stays under the record cap.
    spans, w0 = [], start
    while w0 <= end:
        w1 = min(end, w0 + dt.timedelta(days=DAY_CHUNK - 1))
        spans.append((w0, w1))
        w0 = w1 + dt.timedelta(days=1)
    if len(spans) > 1:
        print(f"gas: {days} days requested, split into {len(spans)} windows "
              f"of up to {DAY_CHUNK} days ({CHUNK} publications x {DAY_CHUNK} "
              f"= {CHUNK * DAY_CHUNK} records, cap ~3,600)")

    raw = {}  # pid -> {date: value}
    for w_start, w_end in spans:
        for i in range(0, len(all_ids), CHUNK):
            for block in _post_gasday(all_ids[i:i + CHUNK], w_start, w_end):
                pid = block.get("publicationId")
                recs = raw.setdefault(pid, {})
                for rec in block.get("publications", []):
                    try:
                        recs[rec.get("applicableFor")] = float(rec.get("value"))
                    except (TypeError, ValueError):
                        continue

    nts = _autoscale(raw.get(nts_id, {}), "NTS total")

    ldz_raw_sum = {}
    for pid, _ in ldz_pubs:
        for d, v in raw.get(pid, {}).items():
            ldz_raw_sum[d] = ldz_raw_sum.get(d, 0.0) + v
    ldz = _autoscale(ldz_raw_sum, "LDZ sum (buildings)")

    return {"nts_demand_actual": nts,
            "ldz_sum": ldz,
            "_meta": {"nts": {"publicationId": nts_id,
                              "publicationName": nts_name},
                      "ldz_zones": sorted(zones),
                      "ldz_publication_count": len(ldz_pubs)}}


if __name__ == "__main__":
    data = fetch_gas_demand(days=14)
    print(json.dumps(data["_meta"], indent=2))

# --- daily SAP series --------------------------------------------------------
# The spark-gap ticker fetches ONE day of System Average Price, so the gas line
# on the heat-price chart could not be drawn historically. SAP comes from the
# same National Gas publication API as the demand series above, so it takes the
# same catalogue lookup and the same date chunking - a range costs one extra
# call per 437 days rather than a new integration.
#
# SAP is a BALANCING price: what National Gas pays to buy or sell gas to
# balance the system on the day. It tracks NBP day-ahead closely but is not
# the same instrument, and that is worth stating wherever the series is drawn.
# VERBATIM from fetch_prices.py - keep in lockstep. Substring patterns, not
# exact names: every string in a pattern must appear in the lowercased
# publication name. The first pattern is the one that hits, and it is why
# three rounds of exact-name guessing failed - the publication is called
# "System Average Price ...", which does not contain the letters "sap" at all,
# so a matcher looking for "sap" could never find it.
SAP_PATTERNS = [["system average price"], ["sap, actual"], ["sap actual"]]


def fetch_gas_sap_series(days=800):
    """Daily System Average Price, p/kWh, for the last `days` days.

    Returns {"dates": [...], "p_per_kwh": [...]}. Missing days are dropped
    rather than filled: a gas price is a published outcome, and inventing one
    would put a fabricated number on a price chart.
    """
    found = _catalogue()
    pub_id = pub_name = None
    for pats in SAP_PATTERNS:
        for pid, name in found:
            low = name.lower()
            if all(x in low for x in pats):
                pub_id, pub_name = pid, name
                break
        if pub_id:
            break
    if not pub_id:
        cand = [n for _, n in found
                if "price" in n.lower() or "sap" in n.lower()]
        print("gas SAP series: no SAP publication matched (%d publications "
              "seen). Price-ish names:" % len(found))
        for n in sorted(cand)[:40]:
            print("    %s" % n)
        return None

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    by_date = {}
    w0 = start
    spans = 0
    while w0 <= end:
        w1 = min(end, w0 + dt.timedelta(days=DAY_CHUNK - 1))
        for block in _post_gasday([pub_id], w0, w1):
            # rows live under "publications", the same key the demand path
            # uses - NOT "data", which was the first guess and returned none
            for rec in block.get("publications", []):
                d_ = (rec.get("applicableFor") or "")[:10]
                try:
                    v = float(rec.get("value"))
                except (TypeError, ValueError):
                    continue
                if d_:
                    by_date[d_] = v
        spans += 1
        w0 = w1 + dt.timedelta(days=1)

    if not by_date:
        print("gas SAP series: publication '%s' (id %s) returned no usable "
              "rows over %s..%s. The demand path's row shape is assumed here "
              "- if SAP uses different field names the values are being "
              "dropped silently, so log one raw row before assuming the feed "
              "is empty." % (pub_name, pub_id, start, end))
        return None
    ds = sorted(by_date)
    vals = [by_date[d_] for d_ in ds]
    # SAME unit sniff as fetch_prices.fetch_gas_sap - keep in lockstep, or the
    # ticker and the chart disagree about what a gas price is. SAP is usually
    # p/kWh (~2-5); p/therm reads ~80-150.
    med = sorted(vals)[len(vals) // 2]
    if med > 20:                         # p/therm
        vals = [v / 29.3071 for v in vals]
        print("gas SAP series: median raw %.1f treated as p/therm -> p/kWh"
              % med)
    print("gas SAP series: %d days, %s to %s, %d requests, min %.2f max %.2f "
          "p/kWh" % (len(ds), ds[0], ds[-1], spans, min(vals), max(vals)))
    return {"dates": ds, "p_per_kwh": [round(v, 4) for v in vals],
            "publication": pub_name}

