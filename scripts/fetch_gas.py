"""Fetch daily GB gas demand from the National Gas Transmission REST API.

Two things fetched daily:
- Total NTS actual demand (context/display)
- Sum of all LDZ actual offtakes (NDM + DM, all zones) = gas to buildings,
  the regression target; excludes directly-connected power stations, so the
  temperature slope is not contaminated by CCGT dispatch.

LDZ publications are self-discovered from the catalogue. Requests are chunked
to respect the ~3,600-record cap. Units auto-detected (kWh / GWh / mcm).
"""

import datetime as dt
import json
import re
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
CHUNK = 8  # publications per request


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


def _post_gasday(pub_ids, start, end):
    r = requests.post(GASDAY_URL, json={
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
        "publicationIds": pub_ids, "latestValue": "Y",
    }, headers={"Content-Type": "application/json"}, timeout=120)
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, list) else payload.get("data", [])


def fetch_gas_demand(days=400):
    r = requests.get(CATALOGUE_URL, timeout=60)
    r.raise_for_status()
    catalogue = []
    _harvest(r.json(), catalogue)
    catalogue = sorted(set(catalogue))
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
    raw = {}  # pid -> {date: value}
    for i in range(0, len(all_ids), CHUNK):
        for block in _post_gasday(all_ids[i:i + CHUNK], start, end):
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
