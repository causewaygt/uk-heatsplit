"""Fetch daily wholesale prices for the heat spark-gap ticker.

Gas: National Gas SAP (System Average Price) via the same operationaldata
API, publication self-resolved from the catalogue Price category.
Electricity: Elexon Insights market index (MID), GET, no key.

Both optional: callers must tolerate failure (ticker simply absent).
Units: returns £/MWh for both.
"""

import datetime as dt
import requests

NG_BASE = "https://api.nationalgas.com/operationaldata/v1"
ELEXON_MID = "https://data.elexon.co.uk/bmrs/api/v1/balancing/pricing/market-index"

SAP_PATTERNS = [["system average price"], ["sap, actual"], ["sap actual"]]


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


def fetch_gas_sap():
    """Latest daily SAP as £/MWh. Raises on failure."""
    r = requests.get(f"{NG_BASE}/publications/catalogue", timeout=60)
    r.raise_for_status()
    catalogue = []
    _harvest(r.json(), catalogue)
    catalogue = sorted(set(catalogue))

    hit = None
    for pats in SAP_PATTERNS:
        for pid, name in catalogue:
            low = name.lower()
            if all(s in low for s in pats):
                hit = (pid, name)
                break
        if hit:
            break
    if not hit:
        cands = "\n".join(n for _, n in catalogue
                          if "price" in n.lower() or "sap" in n.lower())
        raise RuntimeError("SAP publication not found. Price-ish names:\n" + cands)

    end = dt.date.today()
    start = end - dt.timedelta(days=10)
    r = requests.post(f"{NG_BASE}/publications/gasday", json={
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
        "publicationIds": [hit[0]], "latestValue": "Y",
    }, headers={"Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    blocks = payload if isinstance(payload, list) else payload.get("data", [])
    recs = {}
    for block in blocks:
        for rec in block.get("publications", []):
            try:
                recs[rec.get("applicableFor")] = float(rec.get("value"))
            except (TypeError, ValueError):
                continue
    if not recs:
        raise RuntimeError("SAP returned no records")
    date = max(recs)
    v = recs[date]
    # unit sniff: SAP usually p/kWh (~2-5); p/therm ~80-150
    if v > 20:                       # p/therm
        p_per_kwh = v / 29.3071
    else:                            # p/kWh
        p_per_kwh = v
    print(f"gas SAP: {hit[1]} | {date} raw {v} -> {p_per_kwh:.2f} p/kWh")
    return {"date": date, "gbp_per_mwh": round(p_per_kwh * 10.0, 1),
            "publication": hit[1]}


def fetch_elec_mid():
    """Latest daily mean market-index price as £/MWh. Raises on failure."""
    today = dt.date.today()
    for back in range(1, 6):        # walk back to last day with data
        day = today - dt.timedelta(days=back)
        r = requests.get(ELEXON_MID, params={
            "from": f"{day.isoformat()}T00:00:00Z",
            "to": f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z",
        }, timeout=60)
        if r.status_code != 200:
            continue
        data = r.json()
        rows = data.get("data", data if isinstance(data, list) else [])
        prices = [row.get("price") for row in rows
                  if isinstance(row, dict)
                  and isinstance(row.get("price"), (int, float))
                  and (row.get("volume") or 0) > 0]
        if prices:
            avg = sum(prices) / len(prices)
            print(f"elec MID: {day} mean {avg:.1f} £/MWh over {len(prices)} periods")
            return {"date": day.isoformat(), "gbp_per_mwh": round(avg, 1)}
    raise RuntimeError("No MID data in last 5 days")


if __name__ == "__main__":
    print(fetch_gas_sap())
    print(fetch_elec_mid())
