"""UK Heat Split - 12-month hourly retrospective engine (Phase A').

Computes, for the trailing year at hourly resolution, the three-route
20% what-if replacement loads:
  ashp     - air-source heat pumps, COP from hourly air temperature
  shallow  - closed-loop ground source, near-constant source
  network  - SCOP-5 ambient networks (the site's standing what-if case)

DESIGN (all decisions per the v7 plan of record):
- Heat shape is MODELLED (dagger): daily space heat = beta x HDD_d from
  the live regression's constants, distributed within the day by hourly
  heating degree-hours; hot water flat at annual/8760. Price (Elexon
  MID) and carbon (NESO CI) are OBSERVED hourly. The solid/dashed
  grammar on the site reflects exactly this split.
- COP physics: Carnot fraction, weather-compensated flow temperature,
  one eta per route CALIBRATED so the trailing-12-month SPF reproduces
  the published anchors (EoH 2.80 air / ESC 3.24 ground / 5.0 network
  convention). ASHP carries a defrost derating band (shape per the
  heatpumpmonitor.org fleet observation, level re-anchored after
  derating - "shape from the monitor, level from the field trial").
- Gates before anything renders (Phase B refuses to slice on failure):
  G1 each route's integrated SPF within 0.01 of its anchor;
  G2 network-route annual replacement electricity within 3% of the
     weekly model's what-if replacement over the same window.
- Persistence: docs/retro.json - append-only day store, frozen once
  written except the trailing 2 days (feed revisions), schema-versioned
  like the weekly history. COPs are DERIVED at read time from stored
  temperature + calibration, never stored.

SYNC RESOLVED (1 Aug): GB_POINTS below are copied VERBATIM from the
live fetch_degree_days.py (names, coords, raw weights; normalised in
code), and the within-day shaping base is a parameter supplied by the
caller from the live regression's selected base (16.5 at present) -
no hardcoded divergence remains. The hourly temperature fetch also
adopts that module's pattern: one batched Open-Meteo request for all
points, with retries.
"""

import datetime as dt
import json
import math
import os
import urllib.request

# --- constants -------------------------------------------------------------
SPF_ANCHORS = {"ashp": 2.80, "shallow": 3.24, "network": 5.0}
GROUND_SOURCE_C = 8.0        # 11C ground less 3C brine approach
NETWORK_SOURCE_C = 10.0      # shared ambient loop
AIR_APPROACH_C = 3.0         # evaporator approach below air temp
FLOW_MILD_C, FLOW_COLD_C = 30.0, 50.0   # weather compensation endpoints
FLOW_MILD_AT, FLOW_COLD_AT = 15.0, -5.0
DEFROST_MAX = 0.12           # peak fractional COP loss, centre ~2C (dagger)
R_SHIFT = 0.20
RETRO_SCHEMA = 1
RETRO_PATH = os.path.join(os.path.dirname(__file__), "..", "docs",
                          "retro.json")

GB_POINTS = [  # VERBATIM from fetch_degree_days.py - keep in lockstep
    ("London",      51.51,  -0.13, 24.0),
    ("Birmingham",  52.48,  -1.90, 10.0),
    ("Manchester",  53.48,  -2.24, 10.0),
    ("Leeds",       53.80,  -1.55,  7.0),
    ("Glasgow",     55.86,  -4.25,  6.0),
    ("Newcastle",   54.98,  -1.61,  4.0),
    ("Bristol",     51.45,  -2.59,  4.0),
    ("Cardiff",     51.48,  -3.18,  3.5),
    ("Edinburgh",   55.95,  -3.19,  3.5),
    ("Southampton", 50.90,  -1.40,  4.0),
    ("Nottingham",  52.95,  -1.15,  4.0),
    ("Sheffield",   53.38,  -1.47,  4.0),
]


# --- real fetchers (urllib, no new deps; stubs replace these offline) ------
def fetch_hourly_temp(start_day, end_day, retries=4):
    """Population-weighted hourly 2m temperature, ERA5 via Open-Meteo
    archive. One batched request for all GB points with retries -
    same pattern as the daily fetch_degree_days. Hours where under 90%
    of the weight reported are left None for the gap policy."""
    import time
    url = ("https://archive-api.open-meteo.com/v1/archive"
           "?latitude=" + ",".join(str(p[1]) for p in GB_POINTS)
           + "&longitude=" + ",".join(str(p[2]) for p in GB_POINTS)
           + f"&start_date={start_day}&end_date={end_day}"
           "&hourly=temperature_2m&timezone=UTC")
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = json.load(r)
            break
        except Exception as e:            # noqa: BLE001
            last = e
            time.sleep(15 * (attempt + 1))
    else:
        raise last
    results = data if isinstance(data, list) else [data]
    total_w = sum(p[3] for p in GB_POINTS)
    acc = None
    wsum = None
    for (name, lat, lon, w), res in zip(GB_POINTS, results):
        vals = res["hourly"]["temperature_2m"]
        if acc is None:
            acc = [0.0] * len(vals)
            wsum = [0.0] * len(vals)
        for i, v in enumerate(vals):
            if v is not None:
                acc[i] += v * w
                wsum[i] += w
    return [round(a / ws, 2) if ws >= 0.9 * total_w else None
            for a, ws in zip(acc, wsum)]


def fetch_hourly_mid(start_day, end_day):
    """Elexon MID, half-hourly -> hourly mean, GBP/MWh."""
    out = {}
    d0, d1 = dt.date.fromisoformat(start_day), dt.date.fromisoformat(end_day)
    d = d0
    while d <= d1:            # 7-day chunks
        de = min(d + dt.timedelta(days=6), d1)
        url = ("https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
               f"?from={d}&to={de}&dataProviders=APXMIDP&format=json")
        with urllib.request.urlopen(url, timeout=60) as r:
            for rec in json.load(r).get("data", []):
                key = (rec["settlementDate"],
                       (rec["settlementPeriod"] - 1) // 2)
                out.setdefault(key, []).append(rec["price"])
        d = de + dt.timedelta(days=1)
    hours = []
    d = d0
    while d <= d1:
        for h in range(24):
            v = out.get((d.isoformat(), h))
            hours.append(round(sum(v) / len(v), 2) if v else None)
        d += dt.timedelta(days=1)
    return _fill(hours)


def fetch_hourly_ci(start_day, end_day):
    """NESO carbon intensity, half-hourly actuals -> hourly mean, g/kWh."""
    hours = {}
    d0, d1 = dt.date.fromisoformat(start_day), dt.date.fromisoformat(end_day)
    d = d0
    while d <= d1:            # 13-day chunks (API limit 14)
        de = min(d + dt.timedelta(days=12), d1)
        url = (f"https://api.carbonintensity.org.uk/intensity/"
               f"{d}T00:00Z/{de}T23:59Z")
        with urllib.request.urlopen(url, timeout=60) as r:
            for rec in json.load(r)["data"]:
                t = rec["from"][:13]          # YYYY-MM-DDTHH
                v = (rec["intensity"].get("actual")
                     or rec["intensity"].get("forecast"))
                if v is not None:
                    hours.setdefault(t, []).append(v)
        d = de + dt.timedelta(days=1)
    out = []
    d = d0
    while d <= d1:
        for h in range(24):
            v = hours.get(d.isoformat() + "T%02d" % h)
            out.append(round(sum(v) / len(v), 1) if v else None)
        d += dt.timedelta(days=1)
    return _fill(out)


def _fill(xs):
    """Forward/back-fill isolated gaps; refuse >5% missing."""
    n_missing = sum(1 for x in xs if x is None)
    if n_missing > 0.05 * len(xs):
        raise RuntimeError(f"hourly series {n_missing}/{len(xs)} missing")
    last = None
    for i, x in enumerate(xs):
        if x is None:
            xs[i] = last
        else:
            last = x
    for i in range(len(xs) - 1, -1, -1):     # leading gap
        if xs[i] is None:
            xs[i] = last
        else:
            last = xs[i]
    return xs


# --- physics ---------------------------------------------------------------
def flow_temp(t_out):
    f = (FLOW_MILD_AT - t_out) / (FLOW_MILD_AT - FLOW_COLD_AT)
    f = min(1.0, max(0.0, f))
    return FLOW_MILD_C + (FLOW_COLD_C - FLOW_MILD_C) * f


def defrost_factor(t_out):
    """Fractional COP retained. Bell-shaped loss centred ~2C, the humid
    frost band - shape per the heatpumpmonitor.org fleet curve (dagger);
    level is re-anchored by eta calibration afterwards."""
    return 1.0 - DEFROST_MAX * math.exp(-((t_out - 2.0) / 3.0) ** 2)


def carnot_cop(t_flow, t_source):
    dt_lift = max(5.0, t_flow - t_source)
    return (t_flow + 273.15) / dt_lift


def route_cop(route, t_out, eta):
    tf = flow_temp(t_out)
    if route == "ashp":
        base = carnot_cop(tf, t_out - AIR_APPROACH_C)
        return max(1.0, eta * base * defrost_factor(t_out))
    if route == "shallow":
        return max(1.0, eta * carnot_cop(tf, GROUND_SOURCE_C))
    if route == "network":
        return max(1.0, eta * carnot_cop(tf, NETWORK_SOURCE_C))
    raise ValueError(route)


# --- heat shape ------------------------------------------------------------
def hourly_useful_heat(temps, start_day, space_annual_twh, dhw_annual_twh,
                       base_c=16.5):
    """Hourly USEFUL heat, GWh. Space: annual total distributed by daily
    HDD share, then by within-day heating degree-hours at base_c - the
    LIVE regression's selected base, supplied by the caller (dagger -
    modelled shape; misses DHW-and-setback peaks, stated). DHW: flat."""
    n = len(temps)
    days = n // 24
    hdd_day, hdh = [], []
    for i in range(days):
        seg = temps[i * 24:(i + 1) * 24]
        h = [max(0.0, base_c - t) for t in seg]
        hdh.append(h)
        hdd_day.append(sum(h) / 24.0)
    tot_hdd = sum(hdd_day) or 1.0
    dhw_h = dhw_annual_twh * 1000.0 / n
    out = []
    for i in range(days):
        day_space = space_annual_twh * 1000.0 * hdd_day[i] / tot_hdd
        day_hdh = sum(hdh[i]) or 1.0
        for h in range(24):
            sp = day_space * (hdh[i][h] / day_hdh if day_hdh else 0.0)
            out.append(sp + dhw_h)
    return out


# --- calibration -----------------------------------------------------------
def calibrate_eta(route, temps, heat):
    """eta such that annual SPF == anchor: SPF = sum(Q)/sum(Q/COP(t)).
    Monotone in eta -> bisection."""
    target = SPF_ANCHORS[route]

    def spf(eta):
        e = sum(q / route_cop(route, t, eta)
                for q, t in zip(heat, temps) if q > 0)
        return sum(q for q in heat if q > 0) / e

    lo, hi = 0.05, 1.5
    for _ in range(60):
        mid = (lo + hi) / 2
        if spf(mid) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 5)


# --- engine ----------------------------------------------------------------
def build_retro(start_day, end_day, space_annual_twh, dhw_annual_twh,
                base_c=16.5,
                fetch_temp=fetch_hourly_temp, fetch_mid=fetch_hourly_mid,
                fetch_ci=fetch_hourly_ci, prev=None):
    """Build or extend the retrospective store. Frozen once written
    except the trailing 2 days; append-only otherwise."""
    prev = prev if isinstance(prev, dict) else {}
    keep_from = None
    if (prev.get("schema") == RETRO_SCHEMA
            and prev.get("start_day") == start_day
            and prev.get("temp_C")):
        n_prev_days = len(prev["temp_C"]) // 24
        keep_days = max(0, n_prev_days - 2)          # revision window
        keep_from = (dt.date.fromisoformat(start_day)
                     + dt.timedelta(days=keep_days))
    if keep_from and keep_from > dt.date.fromisoformat(end_day):
        keep_from = None

    f0 = keep_from.isoformat() if keep_from else start_day
    temps = _fill(fetch_temp(f0, end_day))
    mids = fetch_mid(f0, end_day)
    cis = fetch_ci(f0, end_day)
    n = min(len(temps), len(mids), len(cis))
    n -= n % 24
    temps, mids, cis = temps[:n], mids[:n], cis[:n]

    if keep_from:
        k = (keep_from - dt.date.fromisoformat(start_day)).days * 24
        temps = prev["temp_C"][:k] + temps
        mids = prev["mid_gbp_mwh"][:k] + mids
        cis = prev["ci_g_kwh"][:k] + cis

    heat = hourly_useful_heat(temps, start_day, space_annual_twh,
                              dhw_annual_twh, base_c=base_c)
    etas = {r: calibrate_eta(r, temps, heat) for r in SPF_ANCHORS}
    spfs = {}
    for r in SPF_ANCHORS:
        e = sum(q / route_cop(r, t, etas[r])
                for q, t in zip(heat, temps) if q > 0)
        spfs[r] = round(sum(q for q in heat if q > 0) / e, 3)

    return {
        "schema": RETRO_SCHEMA,
        "updated": dt.datetime.now(dt.timezone.utc)
                     .strftime("%Y-%m-%dT%H:%MZ"),
        "start_day": start_day,
        "n_hours": len(temps),
        "basis": ("Hourly heat shape modelled (daily HDD share x "
                  "within-day heating degree-hours; DHW flat) - dagger. "
                  "Price (Elexon MID) and carbon (NESO CI) observed "
                  "hourly. Wholesale basis: sits beside the spark gap, "
                  "never the cap-priced bill."),
        "calibration": {
            "eta": etas, "spf_check": spfs, "anchors": SPF_ANCHORS,
            "space_annual_TWh": round(space_annual_twh, 1),
            "shaping_base_c": base_c,
            "dhw_annual_TWh": round(dhw_annual_twh, 1),
            "defrost_max": DEFROST_MAX,
            "flow": [FLOW_MILD_C, FLOW_COLD_C],
        },
        "temp_C": temps,
        "mid_gbp_mwh": mids,
        "ci_g_kwh": cis,
        "heat_GWh": [round(q, 3) for q in heat],
    }


def gates(retro, weekly_whatif_repl_elec_twh=None):
    """G1: anchors reproduced. G2: network annual replacement electricity
    vs the weekly model's what-if (when supplied). Returns (ok, report)."""
    rep = {}
    ok = True
    for r, spf in retro["calibration"]["spf_check"].items():
        d = abs(spf - SPF_ANCHORS[r])
        rep[f"g1_{r}"] = {"spf": spf, "anchor": SPF_ANCHORS[r],
                          "pass": d <= 0.01}
        ok = ok and d <= 0.01
    if weekly_whatif_repl_elec_twh:
        heat, temps = retro["heat_GWh"], retro["temp_C"]
        eta = retro["calibration"]["eta"]["network"]
        elec = sum(R_SHIFT * q / route_cop("network", t, eta)
                   for q, t in zip(heat, temps)) / 1000.0
        d = abs(elec - weekly_whatif_repl_elec_twh) \
            / weekly_whatif_repl_elec_twh
        rep["g2_network_vs_weekly"] = {
            "retro_TWh": round(elec, 2),
            "weekly_TWh": round(weekly_whatif_repl_elec_twh, 2),
            "rel_diff": round(d, 4), "pass": d <= 0.03}
        ok = ok and d <= 0.03
    return ok, rep


def save(retro, path=RETRO_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(retro, f, separators=(",", ":"))
    os.replace(tmp, path)


def load(path=RETRO_PATH):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None
