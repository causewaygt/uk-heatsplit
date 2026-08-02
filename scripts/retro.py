"""UK Heat Split - 12-month hourly retrospective engine (Phase A').

Computes, for the trailing year at hourly resolution, the three-route
20% what-if replacement loads:
  ashp     - air-source heat pumps, COP from hourly air temperature
  shallow  - closed-loop ground source, near-constant source
  network  - SCOP-5 geothermal networks (the site's standing what-if case)

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
import urllib.parse
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
# Summer damping thresholds, daily HDD (dagger - shaping convention,
# chosen to reconcile with the weekly model's zero-space-heat summers)
# B.4 - flow temperature by SERVICE, not by weather alone. Hot water
# needs its cylinder temperature year-round; only space heat follows the
# weather-compensated curve. Before this split, summer DHW was served at
# the 30 C mild-weather flow, which produced absurd summer air-source
# COPs (16+) once B.3 made summer heat 100% DHW.
DHW_FLOW_C = 52.0        # dagger - cylinder flow, year-round
MIN_LIFT_K = 8.0         # dagger - floor on Carnot lift (was 5)
SUMMER_HDD_OFF = 1.0
SUMMER_HDD_FULL = 3.0
# Ceiling dispatchable block (dagger): NESO Winter Outlook 2025/26 base
# case - de-rated margin 6.1 GW at 10.0% of ACS peak => ACS 61.0 GW,
# de-rated supply 67.1 GW; less the Outlook's own wind+solar de-rate
# (~5.1 GW) => ~62 GW of dispatchable-and-interconnector de-rated
# capacity. Winter-assessed: the ceiling and every headroom statement
# are confined to Nov-Mar. Static overlay, not a dispatch model.
DISPATCH_DERATED_GW = 62.0
CEILING_MONTHS = (11, 12, 1, 2, 3)
RETRO_SCHEMA = 2   # 2: system feeds - demand/wind/solar (A'.2)
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
        # All providers, volume-weighted per settlement period - the
        # APXMIDP-only filter left 14% of a 13-month span empty on the
        # first real-data run (1 Aug 2026) and tripped the gap policy.
        # 'to' is midnight-exclusive: a bare date returns the final
        # day empty (every chunk-end Saturday lost 23h on the first two
        # real runs - 1,312 hours, the exact gap count). End-of-day
        # timestamp closes it.
        url = ("https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
               f"?from={d}T00:00Z&to={de}T23:59Z&format=json")
        for _try in range(2):                     # audit F3
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    payload = json.load(r)
                break
            except Exception:
                if _try:
                    raise
                import time as _t; _t.sleep(10)
        for rec in payload.get("data", []):
            key = (rec["settlementDate"],
                   (rec["settlementPeriod"] - 1) // 2)
            vol = rec.get("volume") or 0.0
            out.setdefault(key, []).append((rec["price"], vol))
        d = de + dt.timedelta(days=1)
    hours = []
    missing_days = {}
    d = d0
    while d <= d1:
        for h in range(24):
            v = out.get((d.isoformat(), h))
            if v:
                tv = sum(vol for _, vol in v)
                px = (sum(p * vol for p, vol in v) / tv if tv
                      else sum(p for p, _ in v) / len(v))
                hours.append(round(px, 2))
            else:
                hours.append(None)
                missing_days[d.isoformat()] =                     missing_days.get(d.isoformat(), 0) + 1
        d += dt.timedelta(days=1)
    if missing_days:
        worst = sorted(missing_days.items(),
                       key=lambda kv: -kv[1])[:5]
        print("retro MID gaps:", sum(missing_days.values()), "hours over",
              len(missing_days), "days; worst:", worst)
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
        for _try in range(2):                     # audit F3b
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    payload = json.load(r)
                break
            except Exception:
                if _try:
                    raise
                import time as _t
                _t.sleep(10)
        for rec in payload["data"]:
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
    assert all(x is not None for x in xs), "fill left holes"  # audit F4
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
    dt_lift = max(MIN_LIFT_K, t_flow - t_source)
    return (t_flow + 273.15) / dt_lift


def route_cop(route, t_out, eta, t_flow=None):
    """Point COP at the weather-compensated SPACE flow unless t_flow is
    given (pass DHW_FLOW_C for the hot-water leg)."""
    tf = t_flow if t_flow is not None else flow_temp(t_out)
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
    modelled shape; misses DHW-and-setback peaks, stated). Days below
    SUMMER_HDD_OFF contribute no space heat and the weights are
    renormalised, so summer nights no longer receive winter-strength
    allocations and the annual anchor is still met exactly. DHW: flat."""
    n = len(temps)
    days = n // 24
    hdd_day, hdh = [], []
    for i in range(days):
        seg = temps[i * 24:(i + 1) * 24]
        h = [max(0.0, base_c - t) for t in seg]
        hdh.append(h)
        hdd_day.append(sum(h) / 24.0)
    # Annual quantities are defined on the TRAILING 365 days, so a
    # 13-month store carries 13 months of heat and the trailing year
    # sums to the annual EXACTLY - the gates slice that year.
    # Summer damping (B.3): raw HDD-share allocates full-strength space
    # heat to cool summer nights - behaviourally implausible (systems
    # off, no occupancy model) and INCONSISTENT with the weekly model,
    # whose baseline subtraction reports zero space heat in those weeks.
    # Damp each day's weight by a smooth ramp in daily HDD: nil below
    # HDD_OFF, full above HDD_FULL. Annual conservation is preserved by
    # normalising on the DAMPED weights, so the trailing year still
    # sums exactly to the annual anchor - the damping redistributes
    # into the heating season rather than discarding heat.
    def _damp(hd):
        if hd <= SUMMER_HDD_OFF:
            return 0.0
        if hd >= SUMMER_HDD_FULL:
            return 1.0
        x = (hd - SUMMER_HDD_OFF) / (SUMMER_HDD_FULL - SUMMER_HDD_OFF)
        return x * x * (3 - 2 * x)                   # smoothstep
    wgt = [hd * _damp(hd) for hd in hdd_day]
    den = sum(wgt[-365:]) or 1.0
    dhw_h = dhw_annual_twh * 1000.0 / 8760.0        # rate, not spread
    out = []
    for i in range(days):
        day_space = space_annual_twh * 1000.0 * wgt[i] / den
        day_hdh = sum(hdh[i]) or 1.0
        for h in range(24):
            sp = day_space * (hdh[i][h] / day_hdh if day_hdh else 0.0)
            out.append(sp + dhw_h)
    return out


# --- calibration -----------------------------------------------------------
def _route_base(route, temps, t_flow=None):
    """Per-hour Carnot-with-defrost base b(t): COP = max(1, eta*b(t)).
    t_flow=None uses the weather-compensated space curve; pass
    DHW_FLOW_C for the hot-water leg (B.4). Precomputed once so
    calibration and integration stay fast."""
    out = []
    for t in temps:
        tf = flow_temp(t) if t_flow is None else t_flow
        if route == "ashp":
            b = carnot_cop(tf, t - AIR_APPROACH_C) * defrost_factor(t)
        elif route == "shallow":
            b = carnot_cop(tf, GROUND_SOURCE_C)
        elif route == "network":
            b = carnot_cop(tf, NETWORK_SOURCE_C)
        else:
            raise ValueError(route)
        out.append(b)
    return out


def route_elec_hourly(route, temps, heat, eta, dhw_h, shift=1.0):
    """Hourly electricity for `shift` of the heat, GWh: the space leg on
    the weather-compensated flow, the DHW leg on the cylinder flow, each
    hour's split taken from the constant DHW rate (B.4)."""
    bs = _route_base(route, temps)
    bd = _route_base(route, temps, t_flow=DHW_FLOW_C)
    out = []
    for q, a, b in zip(heat, bs, bd):
        q_dhw = min(q, dhw_h)
        q_sp = max(0.0, q - q_dhw)
        out.append(shift * (q_sp / max(1.0, eta * a)
                            + q_dhw / max(1.0, eta * b)))
    return out


def calibrate_eta(route, temps, heat, dhw_h=0.0):
    """eta such that annual SPF == anchor: SPF = sum(Q)/sum(elec), with
    the space and DHW legs on their own flow temperatures (B.4).
    Monotone in eta -> bisection on the precomputed bases."""
    target = SPF_ANCHORS[route]
    temps = temps[-8760:]          # calibrate on the trailing year
    heat = heat[-8760:]            # (audit F2)
    bs = _route_base(route, temps)
    bd = _route_base(route, temps, t_flow=DHW_FLOW_C)
    qb = [(min(q, dhw_h), max(0.0, q - min(q, dhw_h)), a, b)
          for q, a, b in zip(heat, bs, bd) if q > 0]
    q_tot = sum(qd + qs for qd, qs, _, _ in qb)

    def spf(eta):
        return q_tot / sum(qs / max(1.0, eta * a) + qd / max(1.0, eta * b)
                           for qd, qs, a, b in qb)

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
                base_c=16.5, cooling=None, resistive_space_twh=0.0,
                fetch_temp=fetch_hourly_temp, fetch_mid=fetch_hourly_mid,
                fetch_ci=fetch_hourly_ci, fetch_system=None, prev=None):
    """Build or extend the retrospective store. Frozen once written
    except the trailing 2 days; append-only otherwise."""
    prev = prev if isinstance(prev, dict) else {}
    keep_from = None
    upgrading = (prev.get("schema") == 1 and prev.get("start_day") == start_day
                 and prev.get("temp_C"))
    if ((prev.get("schema") == RETRO_SCHEMA or upgrading)
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

    _fs = fetch_system or fetch_hourly_system
    n_cur = min(len(temps), len(mids), len(cis))
    n_cur -= n_cur % 24
    span_end = (dt.date.fromisoformat(start_day)
                + dt.timedelta(days=n_cur // 24 - 1)).isoformat()
    if keep_from and not upgrading and prev.get("demand_GW"):
        sysd = _fs(keep_from.isoformat(), span_end)
        k = (keep_from - dt.date.fromisoformat(start_day)).days * 24
        demand = prev["demand_GW"][:k] + sysd["demand_GW"]
        wind = prev["wind_GW"][:k] + sysd["wind_GW"]
        solar = prev["solar_GW"][:k] + sysd["solar_GW"]
    else:
        sysd = _fs(start_day, span_end)
        demand, wind, solar = (sysd["demand_GW"], sysd["wind_GW"],
                               sysd["solar_GW"])

    heat = hourly_useful_heat(temps, start_day, space_annual_twh,
                              dhw_annual_twh, base_c=base_c)
    dhw_h_rate = dhw_annual_twh * 1000.0 / 8760.0
    etas = {r: calibrate_eta(r, temps, heat, dhw_h=dhw_h_rate)
            for r in SPF_ANCHORS}
    spfs = {}
    h_y, t_y = heat[-8760:], temps[-8760:]
    for r in SPF_ANCHORS:
        e = sum(route_elec_hourly(r, t_y, h_y, etas[r], dhw_h_rate))
        spfs[r] = round(sum(q for q in h_y if q > 0) / e, 3)

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
            "resistive_space_TWh": round(resistive_space_twh, 1),
            "shaping_base_c": base_c,
            "dhw_annual_TWh": round(dhw_annual_twh, 1),
            "defrost_max": DEFROST_MAX,
            "cooling": cooling,   # production convention, provenance only:
                                  # series derived at slice time from temps
            "flow": [FLOW_MILD_C, FLOW_COLD_C],
            "flow_at": [FLOW_MILD_AT, FLOW_COLD_AT],
            "sources": {"air_approach": AIR_APPROACH_C,
                        "ground_c": GROUND_SOURCE_C,
                        "network_c": NETWORK_SOURCE_C},
        },
        "temp_C": temps,
        "mid_gbp_mwh": mids,
        "ci_g_kwh": cis,
        "heat_GWh": [round(q, 3) for q in heat],
        "demand_GW": demand[:len(temps)],
        "wind_GW": wind[:len(temps)],
        "solar_GW": solar[:len(temps)],
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
        heat = retro["heat_GWh"][-8760:]
        temps = retro["temp_C"][-8760:]
        eta = retro["calibration"]["eta"]["network"]
        dhw_h = retro["calibration"]["dhw_annual_TWh"] * 1000.0 / 8760.0
        elec = sum(route_elec_hourly("network", temps, heat, eta, dhw_h,
                                     shift=R_SHIFT)) / 1000.0
        d = abs(elec - weekly_whatif_repl_elec_twh) \
            / weekly_whatif_repl_elec_twh
        rep["g2_network_vs_weekly"] = {
            "retro_TWh": round(elec, 2),
            "weekly_TWh": round(weekly_whatif_repl_elec_twh, 2),
            "rel_diff": round(d, 4), "pass": d <= 0.03}
        ok = ok and d <= 0.03
    return ok, rep


def save(retro, path=None):
    path = path or RETRO_PATH
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(retro, f, separators=(",", ":"))
    os.replace(tmp, path)


def load(path=None):
    path = path or RETRO_PATH
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


# ===========================================================================
# Phase B - slice and stress
# ===========================================================================
MAX_DAYS = 400               # store cap; oldest days trimmed past this


def trim(store, max_days=MAX_DAYS):
    """Drop whole days from the front beyond the cap; adjust start_day."""
    n_days = store["n_hours"] // 24
    if n_days <= max_days:
        return store
    cut = (n_days - max_days) * 24
    for k in ("temp_C", "mid_gbp_mwh", "ci_g_kwh", "heat_GWh",
              "demand_GW", "wind_GW", "solar_GW"):
        store[k] = store[k][cut:]
    store["start_day"] = (dt.date.fromisoformat(store["start_day"])
                          + dt.timedelta(days=n_days - max_days)).isoformat()
    store["n_hours"] = len(store["temp_C"])
    return store


def _route_elec(store, route):
    """Hourly what-if replacement electricity, GWh (== average GW)."""
    cal = store["calibration"]
    dhw_h = cal["dhw_annual_TWh"] * 1000.0 / 8760.0
    return route_elec_hourly(route, store["temp_C"], store["heat_GWh"],
                             cal["eta"][route], dhw_h, shift=R_SHIFT)


ROUTES = ("ashp", "shallow", "network")


def hourly_cooling_elec(temps, cooling):
    """Hourly cooling ELECTRICITY, GWh - the production convention
    mirrored: half the ECUK annual flat (ventilation/base load), half
    shaped by cooling degree-hours at the production base. Embedded in
    observed demand, unlike the heat routes' ADDED load - stated
    wherever shown."""
    ann = cooling["annual_gwh"]
    base = cooling["base_c"]
    cdh = [max(0.0, t - base) for t in temps]
    den = sum(cdh[-8760:]) or 1.0            # trailing-365d convention
    flat_h = 0.5 * ann / 8760.0              # rate, not spread
    shaped = [0.5 * ann * c / den for c in cdh]
    return flat_h, shaped


def slices(store, nd_daily=None):
    """Everything Phase C renders, from the stored year.
    nd_daily: {date_iso: observed GB daily demand GWh} for the ceilings
    (daily-average basis, stated - hourly observed demand is not a feed
    this engine carries)."""
    n = store["n_hours"]
    d0 = dt.date.fromisoformat(store["start_day"])
    elec = {r: _route_elec(store, r) for r in ROUTES}
    temps, heat = store["temp_C"], store["heat_GWh"]
    mid, ci = store["mid_gbp_mwh"], store["ci_g_kwh"]

    # ---- cooling layer (embedded, not added - production convention) ----
    cooling = store["calibration"].get("cooling")
    cool = None
    if cooling:
        c_flat_h, c_shaped = hourly_cooling_elec(temps, cooling)
        cool = [c_flat_h + sh for sh in c_shaped]


    def _iso(i):
        return (d0 + dt.timedelta(days=i // 24)).isoformat() + "T%02d" % (i % 24)

    # ---- worst hour + worst week, empirically located ----
    # Searches confined to the trailing year: a 13-month store holds a
    # duplicate calendar month and must not offer it twice (audit F1b).
    s0 = max(0, n - 8760)
    iw = max(range(s0, n), key=lambda i: heat[i])
    iww, best = s0, -1.0
    if n - s0 >= 168:
        run = sum(heat[s0:s0 + 168])
        iww, best = s0, run
        for i in range(s0 + 1, n - 167):
            run += heat[i + 167] - heat[i - 1]
            if run > best:
                best, iww = run, i

    def _window(i0, ln):
        return {
            "start": _iso(i0),
            "temp_C": temps[i0:i0 + ln],
            "heat_GWh": [round(v, 2) for v in heat[i0:i0 + ln]],
            "mid_gbp_mwh": mid[i0:i0 + ln],
            "ci_g_kwh": ci[i0:i0 + ln],
            "routes": {r: [round(v, 3) for v in elec[r][i0:i0 + ln]]
                       for r in ROUTES},
            "cool_GWh": ([round(v, 3) for v in cool[i0:i0 + ln]]
                         if cool else None),
            **({"demand_GW": store["demand_GW"][i0:i0 + ln],
                "wind_GW": store["wind_GW"][i0:i0 + ln],
                "solar_GW": store["solar_GW"][i0:i0 + ln],
                "displaced_GW": [round(v, 3) for v in
                                 _displaced_gw(store)[i0:i0 + ln]]}
               if store.get("demand_GW") else {}),
        }

    hourly_live = _window(n - 168, 168)
    worst_week = _window(iww, 168)

    # The exhibit week: 168 h centred on the tightest hour for the
    # system (the air-source binding hour), clipped to the store. The
    # max-heat week and the binding week need not coincide - post-B.4
    # they did not - and the ceiling chart must contain the hour its
    # own cards describe.
    binding_week = None
    sysv = system_view(store) if store.get("demand_GW") else None
    if sysv:
        bh = sysv["routes"]["ashp"]["binding_hour"]
        bday = dt.date.fromisoformat(bh[:10])
        ib = (bday - d0).days * 24 + int(bh[11:13])
        i0 = min(max(0, ib - 84), n - 168)
        binding_week = _window(i0, 168)
        binding_week["binding_hour"] = bh

    # ---- daily (last 90) and monthly (13 calendar months) ----
    def _agg(i0, i1):
        seg = slice(i0, i1)
        out = {"heat_GWh": round(sum(heat[seg]), 1),
               "temp_mean_C": round(sum(temps[seg]) / (i1 - i0), 2)}
        if cool:
            c_e = sum(cool[seg])
            fl = c_flat_h * (i1 - i0)
            u = fl * 1.0 + (c_e - fl) * cooling["eer"]  # useful, site conv.
            out["cool_GWh"] = round(c_e, 2)
            out["cool_whatif_relief_GWh"] = round(
                R_SHIFT * (c_e - u / cooling["passive_cop"]), 2)
        for r in ROUTES:
            e = elec[r][seg]
            out[r + "_GWh"] = round(sum(e), 2)
            out[r + "_cost_Mgbp"] = round(sum(
                v * m for v, m in zip(e, mid[seg])) / 1000.0, 2)
            out[r + "_kt"] = round(sum(
                v * c for v, c in zip(e, ci[seg])) / 1000.0, 2)
        return out

    days_n = n // 24
    daily = []
    for di in range(max(0, days_n - 90), days_n):
        row = _agg(di * 24, (di + 1) * 24)
        row["date"] = (d0 + dt.timedelta(days=di)).isoformat()
        daily.append(row)

    import calendar as _cal
    monthly = []
    mkey = None
    mstart = 0
    for di in range(days_n + 1):
        d = d0 + dt.timedelta(days=di)
        k = (d.year, d.month) if di < days_n else None
        if k != mkey:
            if mkey is not None:
                row = _agg(mstart * 24, di * 24)
                row["month"] = "%04d-%02d" % mkey
                row["complete"] = (di - mstart
                                   == _cal.monthrange(*mkey)[1])
                monthly.append(row)
            mkey, mstart = k, di
    monthly = monthly[-13:]

    # Calendar-ordered falcon: the latest 12 COMPLETE months, one per
    # calendar position, reordered Jan -> Dec (years mixed, labelled).
    comp = [r for r in monthly if r.get("complete")][-12:]
    monthly_calendar = None
    if len(comp) == 12 and len({r["month"][5:] for r in comp}) == 12:
        monthly_calendar = sorted(comp, key=lambda r: r["month"][5:])
        MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for r in monthly_calendar:
            r["label"] = (MN[int(r["month"][5:]) - 1] + " "
                          + r["month"][2:4])

    # ---- stress at the worst hour (daily-average demand basis) ----
    stress = {"worst_hour": _iso(iw),
              "worst_hour_heat_GWh": round(heat[iw], 1),
              "worst_hour_temp_C": temps[iw],
              "basis": ("Added load per route at the year's highest-"
                        "demand modelled hour (dagger). Ceilings on a "
                        "OBSERVED-HOURLY basis - the hour itself and "
                        "the trailing year's record hour. Gross here; "
                        "net-of-displaced and the binding hour live in "
                        "the System view.")}
    D_obs = store.get("demand_GW")
    if D_obs:
        rec_i = max(range(max(0, n - 8760), n), key=lambda i: D_obs[i])
        stress["hour_demand_GW"] = round(D_obs[iw], 1)
        stress["record_hour_GW"] = round(D_obs[rec_i], 1)
        stress["record_hour"] = _iso(rec_i)
    elif nd_daily:
        day_iso = _iso(iw)[:10]
        same_day_gw = (nd_daily.get(day_iso, 0.0)) / 24.0
        rec_gw = max(nd_daily.values()) / 24.0
        stress["same_day_avg_GW"] = round(same_day_gw, 1)
        stress["record_day_avg_GW"] = round(rec_gw, 1)
    for r in ROUTES:
        add = elec[r][iw]
        stress[r] = {"added_GW": round(add, 1)}
        if stress.get("hour_demand_GW"):
            stress[r]["pct_of_hour"] = round(
                100 * add / stress["hour_demand_GW"], 1)
            stress[r]["pct_of_record_hour"] = round(
                100 * add / stress["record_hour_GW"], 1)
        elif nd_daily and stress.get("same_day_avg_GW"):
            stress[r]["pct_of_same_day"] = round(
                100 * add / stress["same_day_avg_GW"], 1)
            stress[r]["pct_of_record_day"] = round(
                100 * add / stress["record_day_avg_GW"], 1)
        # hours-above-threshold distribution: added GW exceeded for X h
        stress[r]["hours_above"] = {
            str(g): sum(1 for v in elec[r][max(0, n - 8760):] if v > g)
            for g in (5, 10, 15, 20, 25)}   # trailing year (audit F1)

    # ---- summer stress: the cooling peak (embedded basis, stated) ----
    stress_summer = None
    if cool:
        ic = max(range(s0, n), key=lambda i: cool[i])   # audit F1b
        u_pk = (c_flat_h * 1.0
                + (cool[ic] - c_flat_h) * cooling["eer"])
        relief_pk = R_SHIFT * (cool[ic] - u_pk / cooling["passive_cop"])
        stress_summer = {
            "peak_hour": _iso(ic),
            "peak_temp_C": temps[ic],
            "today_GW": round(cool[ic], 2),
            "hour_demand_GW": (round(store["demand_GW"][ic], 1)
                               if store.get("demand_GW") else None),
            "x2_scenario_added_GW": round(cool[ic], 2),
            "whatif_relief_GW": round(relief_pk, 2),
            "basis": ("Cooling is EMBEDDED in observed demand - the "
                      "opposite accounting to the heat routes' added "
                      "load. today_GW: the production-convention "
                      "estimate at the year's peak cooling hour. "
                      "x2_scenario: the on-record dagger prediction "
                      "(annual share doubles within a decade) applied "
                      "as a uniform doubling - added load equals "
                      "today's. whatif_relief: 20% of cooling moved "
                      "near-passive (COP " +
                      str(cooling["passive_cop"]) + ") REMOVES this "
                      "much at the same hour.")}
        if stress_summer.get("hour_demand_GW"):
            stress_summer["today_pct_of_hour"] = round(
                100 * cool[ic] / stress_summer["hour_demand_GW"], 1)
        elif nd_daily:
            day_iso = _iso(ic)[:10]
            sd = nd_daily.get(day_iso, 0.0) / 24.0
            if sd:
                stress_summer["today_pct_of_same_day"] = round(
                    100 * cool[ic] / sd, 2)

    # ---- coincidence cost premium ----
    # Trailing-year window for all "annual" statements - a 13-month
    # store must never masquerade as a year (audit F1).
    Y = slice(max(0, n - 8760), n)
    mid_y = mid[Y]
    mean_mid = sum(mid_y) / len(mid_y)
    premium = {}
    for r in ROUTES:
        e = elec[r][Y]
        hourly_cost = sum(v * m for v, m in zip(e, mid_y)) / 1000.0
        flat_cost = sum(e) * mean_mid / 1000.0
        premium[r] = {
            "annual_GWh": round(sum(e), 0),
            "window": "trailing 12 months",
            "hourly_priced_Mgbp": round(hourly_cost, 1),
            "flat_priced_Mgbp": round(flat_cost, 1),
            "premium_pct": round(100 * (hourly_cost / flat_cost - 1), 2)
            if flat_cost else None,
        }
    premium["note"] = (
        "The coincidence premium: each route's replacement electricity "
        "priced at the hour it is drawn (Elexon MID) vs the same annual "
        "energy at the flat mean price. Cold-evening concentration makes "
        "air-source dearest per unit; source temperature flattens the "
        "draw. Wholesale basis - sits beside the spark gap.")

    return {
        "schema": 2,
        "basis": store["basis"],
        "system": sysv,
        "calibration": store["calibration"],
        "hourly_live_7d": hourly_live,
        "worst_week": worst_week,
        "binding_week": binding_week,
        "daily_90": daily,
        "monthly_13": monthly,
        "monthly_calendar": monthly_calendar,
        "stress": stress,
        "stress_summer": stress_summer,
        "coincidence_premium": premium,
    }


# ===========================================================================
# A'.2 - the system feeds: observed hourly demand, wind, solar (schema 2)
# ===========================================================================
def fetch_hourly_system(start_day, end_day):
    """Observed system series, hourly GW:
      demand_GW - UNDERLYING demand: NESO ND plus embedded wind and solar
        (both sides of the ledger carry embedded; basis stated on-panel)
      wind_GW   - transmission-metered wind (Elexon FUELINST) + embedded
        wind (NESO historic demand file)
      solar_GW  - embedded solar (NESO file; transmission solar immaterial)
    NESO resources are discovered via CKAN package_show on the stable
    'historic-demand-data' slug - no hardcoded resource ids (they churn
    yearly). VERIFY-ON-DISPATCH: field names below match the published
    demanddata schema; the first branch run confirms or corrects."""
    import time
    d0 = dt.date.fromisoformat(start_day)
    d1 = dt.date.fromisoformat(end_day)

    # ---- NESO: ND + embedded wind/solar, half-hourly ----
    def _ckan(url):
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    return json.load(r)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(10 * (attempt + 1))
    pkg = _ckan("https://api.neso.energy/api/3/action/package_show"
                "?id=historic-demand-data")
    res = {r["name"].lower(): r["id"]
           for r in pkg["result"]["resources"]}
    need_years = {str(y) for y in range(d0.year, d1.year + 1)}
    hh = {}
    for name, rid in res.items():
        if not any(y in name for y in need_years):
            continue
        sql = ("SELECT \"SETTLEMENT_DATE\",\"SETTLEMENT_PERIOD\",\"ND\","
               "\"EMBEDDED_WIND_GENERATION\",\"EMBEDDED_SOLAR_GENERATION\" "
               f"FROM \"{rid}\" "
               f"WHERE \"SETTLEMENT_DATE\" >= '{d0}' "
               f"AND \"SETTLEMENT_DATE\" <= '{d1}'")
        data = _ckan("https://api.neso.energy/api/3/action/"
                     "datastore_search_sql?sql="
                     + urllib.parse.quote(sql))
        for rec in data["result"]["records"]:
            sd = str(rec["SETTLEMENT_DATE"])[:10]
            key = (sd, (int(rec["SETTLEMENT_PERIOD"]) - 1) // 2)
            hh.setdefault(key, []).append(
                (float(rec["ND"] or 0),
                 float(rec["EMBEDDED_WIND_GENERATION"] or 0),
                 float(rec["EMBEDDED_SOLAR_GENERATION"] or 0)))

    # ---- NESO tail: the historic year-file lags ~2-3 weeks; the
    # rolling demand-data-update dataset covers the gap (first schema-2
    # dispatch: 481 missing hours = 20 whole days + one DST hour).
    # Same discovery, same schema, merged where the year-files are dark.
    have_dates = {k[0] for k in hh}
    missing = [(d0 + dt.timedelta(days=i)).isoformat()
               for i in range((d1 - d0).days + 1)
               if (d0 + dt.timedelta(days=i)).isoformat() not in have_dates]
    if missing:
        try:
            # slug verified from NESO's portal page (data-portal/
            # daily-demand-update/demand_data_update), 3 Aug 2026
            pkg_u = _ckan("https://api.neso.energy/api/3/action/"
                          "package_show?id=daily-demand-update")
            for r_u in pkg_u["result"]["resources"]:
                rid = r_u["id"]
                sql = ("SELECT \"SETTLEMENT_DATE\",\"SETTLEMENT_PERIOD\","
                       "\"ND\",\"EMBEDDED_WIND_GENERATION\","
                       "\"EMBEDDED_SOLAR_GENERATION\" "
                       f"FROM \"{rid}\" "
                       f"WHERE \"SETTLEMENT_DATE\" >= '{missing[0]}' "
                       f"AND \"SETTLEMENT_DATE\" <= '{missing[-1]}'")
                try:
                    data = _ckan("https://api.neso.energy/api/3/action/"
                                 "datastore_search_sql?sql="
                                 + urllib.parse.quote(sql))
                except Exception:
                    continue
                for rec in data["result"]["records"]:
                    sd = str(rec["SETTLEMENT_DATE"])[:10]
                    key = (sd, (int(rec["SETTLEMENT_PERIOD"]) - 1) // 2)
                    if key[0] not in have_dates:
                        hh.setdefault(key, []).append(
                            (float(rec["ND"] or 0),
                             float(rec["EMBEDDED_WIND_GENERATION"] or 0),
                             float(rec["EMBEDDED_SOLAR_GENERATION"] or 0)))
                if data["result"]["records"]:
                    break
            got = {k[0] for k in hh} - have_dates
            print("retro system tail: demand-data-update supplied",
                  len(got), "of", len(missing), "lagging days")
        except Exception as e:                    # noqa: BLE001
            print("retro system tail: update dataset unavailable:", e)

    # ---- Elexon FUELINST: transmission wind, chunked with retries ----
    tw = {}
    d = d0
    while d <= d1:
        de = min(d + dt.timedelta(days=6), d1)
        url = ("https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST"
               f"?publishDateTimeFrom={d}T00:00Z"
               f"&publishDateTimeTo={de}T23:59Z"
               "&fuelType=WIND&format=json")
        for _try in range(2):
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    payload = json.load(r)
                break
            except Exception:
                if _try:
                    raise
                time.sleep(10)
        for rec in payload.get("data", []):
            t = str(rec.get("startTime") or rec.get("publishTime"))[:13]
            g = rec.get("generation")
            if g is not None:
                tw.setdefault(t, []).append(float(g))
        d = de + dt.timedelta(days=1)

    demand, wind, solar = [], [], []
    d = d0
    while d <= d1:
        for h in range(24):
            v = hh.get((d.isoformat(), h))
            twv = tw.get(d.isoformat() + "T%02d" % h)
            if v:
                nd = sum(x[0] for x in v) / len(v) / 1000.0
                ew = sum(x[1] for x in v) / len(v) / 1000.0
                es = sum(x[2] for x in v) / len(v) / 1000.0
                demand.append(round(nd + ew + es, 2))
                solar.append(round(es, 2))
                wind.append(round(ew + (sum(twv) / len(twv) / 1000.0
                                        if twv else 0.0), 2)
                            if twv or v else None)
            else:
                demand.append(None)
                solar.append(None)
                wind.append(None)
        d += dt.timedelta(days=1)
    return {"demand_GW": _fill(demand), "wind_GW": _fill(wind),
            "solar_GW": _fill(solar)}


# ===========================================================================
# B.2 - netting, hourly stress re-base, the ceiling, the binding hour
# ===========================================================================
def _displaced_gw(store):
    """Hourly electricity the what-if REMOVES from observed demand: the
    fifth of existing resistive space heating, shaped like space heat.
    Route-independent (the displaced kit is the same whichever route
    replaces it)."""
    cal = store["calibration"]
    res = cal.get("resistive_space_TWh", 0.0)
    if not res:
        return [0.0] * store["n_hours"]
    dhw_h = cal["dhw_annual_TWh"] * 1000.0 / 8760.0
    space_u = cal["space_annual_TWh"] * 1000.0
    return [R_SHIFT * res * 1000.0
            * max(0.0, q - dhw_h) / space_u
            for q in store["heat_GWh"]]


def system_view(store):
    """Everything the System panel renders (slices schema 2 material):
    net route additions on observed demand under the breathing ceiling.
    All statistics trailing-year; ceiling statements Nov-Mar only."""
    n = store["n_hours"]
    d0 = dt.date.fromisoformat(store["start_day"])
    D, W, S = store["demand_GW"], store["wind_GW"], store["solar_GW"]
    disp = _displaced_gw(store)
    gross = {r: _route_elec(store, r) for r in ROUTES}
    net = {r: [g - d for g, d in zip(gross[r], disp)] for r in ROUTES}

    def _iso(i):
        return ((d0 + dt.timedelta(days=i // 24)).isoformat()
                + "T%02d" % (i % 24))

    s0 = max(0, n - 8760)
    Yr = range(s0, n)
    winter = [i for i in Yr
              if (d0 + dt.timedelta(days=i // 24)).month in CEILING_MONTHS]

    # record observed hour (trailing year)
    irec = max(Yr, key=lambda i: D[i])

    out = {
        "dispatch_derated_GW": DISPATCH_DERATED_GW,
        "record_hour": {"hour": _iso(irec), "demand_GW": round(D[irec], 1)},
        "displaced_note": (
            "Net additions: the what-if removes the displaced fifth of "
            "existing resistive space heating (route-independent) from "
            "the same observed demand it sits in; gross additions are "
            "retained for comparison. Displaced resistive is space-"
            "shaped (dagger): night-storage tariff profiles would "
            "reduce the credit at morning and evening peaks - the "
            "gross figures bound the zero-credit case."),
        "basis": (
            "System view basis: observed hourly underlying demand (NESO "
            "ND + embedded wind and solar - embedded generation counted "
            "on both sides of the ledger), observed hourly wind "
            "(transmission + embedded) and solar. Ceiling = a fixed "
            "dispatchable-and-interconnector de-rated block (dagger - "
            "NESO Winter Outlook 2025/26 arithmetic, stated in Method) "
            "plus OBSERVED wind and solar - no wind de-rating "
            "assumption to dispute; the ceiling sags when the wind "
            "actually did not blow. Winter-assessed block: ceiling "
            "statements confined to Nov-Mar. Static overlay, not a "
            "dispatch model - no redispatch, imports response or price "
            "response; distribution constraints unmodelled; a GW "
            "ceiling cannot represent storage DURATION - the worst "
            "week's multi-day draw is what duration-limited storage "
            "cannot cover. One-winter sample (dagger): 2025-26's cold "
            "snap was windy; its still spell (18-21 Mar, wind to 1.6 "
            "GW) was mild; a year where they coincide is the risk case "
            "this chart lets the reader construct."),
        "routes": {},
    }

    for r in ROUTES:
        ib = max(winter, key=lambda i: D[i] + net[r][i] - W[i] - S[i])
        req = D[ib] + net[r][ib] - W[ib] - S[ib]
        exceed = sum(1 for i in winter
                     if D[i] + net[r][i] - W[i] - S[i]
                     > DISPATCH_DERATED_GW)
        out["routes"][r] = {
            "binding_hour": _iso(ib),
            "binding_temp_C": store["temp_C"][ib],
            "binding_demand_GW": round(D[ib], 1),
            "binding_wind_GW": round(W[ib], 1),
            "binding_solar_GW": round(S[ib], 2),
            "net_add_GW": round(net[r][ib], 1),
            "gross_add_GW": round(gross[r][ib], 1),
            "dispatch_req_GW": round(req, 1),
            "headroom_GW": round(DISPATCH_DERATED_GW - req, 1),
            "hours_above_block": exceed,
        }
    return out
