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
     weekly model's what-if replacement over the same window;
  G5 the three routes' calibrated Carnot fractions within 15% of their
     mean - they describe machine quality, which does not differ by
     route, so a spread means an anchor and a source that disagree.
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
NETWORK_SOURCE_C = 19.6      # dagger - UTES and intermediate-doublet
                             # blend, NOT virgin ground. A shared loop
                             # coupled to seasonal storage is recharged
                             # by summer cooling rejection, so it runs
                             # warmer than the 8C shallow figure. 19.6
                             # is DERIVED, not measured: it is the
                             # source at which the network route's
                             # calibrated Carnot fraction matches the
                             # other two (see G5). At 10C the network
                             # needed 0.487 against 0.332/0.336 for air
                             # and shallow - a 45% better machine for a
                             # 2K better source, which is not physical.
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
# --- the ceiling, per winter -------------------------------------------------
# The site's ceiling is the de-rated DISPATCHABLE block plus the wind and solar
# ACTUALLY generated in each hour. So this constant must exclude wind, or wind
# is counted twice: once de-rated in the block and again at its outturn.
#
# Derived the same way for both winters, from NESO's published Winter Outlook:
#     total de-rated capacity = ACS peak demand + de-rated margin
#     dispatchable block      = total - de-rated wind
#
# 2024/25 (WO 8 Oct 2024): margin 5.2 GW at 8.8% of ACS peak, so ACS peak
#   59.8 GW and total 65.0. NESO de-rated 30,532 MW of installed wind to
#   4,416 MW, giving 60.6 GW dispatchable.
# 2025/26 (WO 9 Oct 2025): margin 6.1 GW at 10.0%, so ACS peak 61.0 GW and
#   total 67.1. De-rated wind is not published in the summaries; scaled with
#   installed capacity growth to ~4.8 GW, giving 62.3 GW. DAGGERED - the wind
#   de-rating is the estimated part, not the margin.
#
# Each winter is now tested against ITS OWN fleet. Before 12 Aug 2026 a single
# 62.0 was applied to every hour, which was defensible while the store held one
# winter and became wrong the moment it held two.
DISPATCH_BY_WINTER = {
    2024: 60.6,      # winter 2024/25, labelled by its OPENING year
    2025: 62.3,      # winter 2025/26
}
DISPATCH_DERATED_GW = 62.3       # fallback and headline; the latest winter


def dispatch_block(when):
    """De-rated dispatchable capacity for the winter containing `when`.

    Winters are labelled by their opening year, so January 2026 belongs to
    winter 2025. Anything outside the table falls back to the latest figure
    rather than failing - an unmapped year is a stale table, not a data error.
    """
    y = when.year - (1 if when.month <= 6 else 0)
    return DISPATCH_BY_WINTER.get(y, DISPATCH_DERATED_GW)
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


ETA_SPREAD_MAX = 0.15        # G5 - see below


def gates(retro, weekly_whatif_repl_elec_twh=None):
    """G1: anchors reproduced. G2: network annual replacement electricity
    vs the weekly model's what-if (when supplied). G5: the three routes'
    calibrated Carnot fractions are mutually consistent. Returns
    (ok, report)."""
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

    # G5 - CALIBRATION CONSISTENCY. The Carnot fraction is a statement
    # about MACHINE QUALITY. All three routes use the same class of
    # vapour-compression plant, so their fractions should agree; what
    # should differ between routes is the SOURCE TEMPERATURE, which the
    # Carnot term already carries. A fraction well away from its peers
    # means an anchor and a source assumption that cannot both be true.
    # This gate exists because exactly that went unnoticed: the network
    # route ran at 0.487 against 0.332 and 0.336, a 45% better machine
    # for a 2 K better source, and nothing in the build compared them.
    etas = retro["calibration"]["eta"]
    vals = [v for v in etas.values() if v]
    if vals:
        mean = sum(vals) / len(vals)
        spread = max(abs(v - mean) / mean for v in vals)
        rep["g5_eta_consistency"] = {
            "eta": {k: round(v, 5) for k, v in etas.items()},
            "mean": round(mean, 5),
            "max_rel_dev": round(spread, 4),
            "limit": ETA_SPREAD_MAX,
            "pass": spread <= ETA_SPREAD_MAX}
        ok = ok and spread <= ETA_SPREAD_MAX
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
# Store cap. Raised 400 -> 800 on 12 Aug 2026 to reach two winters, so the
# worst hour, the binding hour and the tightest week become a choice across
# winters rather than a description of one.
#
# THIS ALONE DOES NOT REACH BACKWARDS. start_day is fixed when the store is
# created and trim() only ever moves it FORWARD. Raising the cap stops the
# front being cut, so the store grows forward one day per run from where it
# is. Reaching winter 24/25 needs the separate backfill below, which moves
# r_start and therefore refetches the whole span.
MAX_DAYS = 800               # store cap; oldest days trimmed past this


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

# The coincidence premium is computed over the heating season only.
# Oct-Mar deliberately matches the NESO Winter Outlook window that sets
# the capacity line elsewhere on this page, so the two statistics answer
# for the same months. (9 Aug 2026 - see the premium block for why.)
WINTER_MONTHS = (10, 11, 12, 1, 2, 3)
WINTER_LABEL = "October to March"


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


def slices(store, nd_daily=None, sap_series=None, retail_series=None):
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
    #
    # WINTER BASIS (9 Aug 2026). Computed over the trailing year, this
    # statistic was being diluted to nothing by summer hours in which
    # almost no heat is drawn: it slid 1.42/0.90/0.93 -> 0.39/-0.05/-0.02
    # -> 0.24/-0.19/-0.15 across three runs and went negative, and a
    # negative premium is a contradiction on the page. It is now computed
    # over WINTER_MONTHS only, which is where the coincidence it describes
    # actually happens.
    #
    # The flat comparator uses the mean price of THE SAME HOURS, not the
    # annual mean. That is deliberate and it narrows the statistic: it now
    # measures timing WITHIN the heating season - whether a route draws in
    # that season's dear hours - and excludes the winter-vs-summer price
    # level, which is a seasonal effect rather than a coincidence. Pricing
    # a winter-only draw against an annual mean would fold the two
    # together and inflate the result. The trailing-year figure is kept
    # below as a diagnostic so the change is auditable, not silent.
    y0 = max(0, n - 8760)

    def _premium_over(idx, window_label):
        """Premium across the hour indices `idx`, priced against the flat
        mean of those same hours. Returns None if the window is empty."""
        if not idx:
            return None
        mean_p = sum(mid[i] for i in idx) / len(idx)
        if not mean_p:
            return None
        block = {}
        for r in ROUTES:
            e = [elec[r][i] for i in idx]
            hourly_cost = sum(elec[r][i] * mid[i] for i in idx) / 1000.0
            flat_cost = sum(e) * mean_p / 1000.0
            block[r] = {
                "GWh": round(sum(e), 0),
                "window": window_label,
                "hours": len(idx),
                "mean_price_gbp_mwh": round(mean_p, 1),
                "hourly_priced_Mgbp": round(hourly_cost, 1),
                "flat_priced_Mgbp": round(flat_cost, 1),
                "premium_pct": round(100 * (hourly_cost / flat_cost - 1), 2)
                if flat_cost else None,
            }
        return block

    w_idx = [i for i in range(y0, n)
             if (d0 + dt.timedelta(days=i // 24)).month in WINTER_MONTHS]
    premium = _premium_over(w_idx, WINTER_LABEL) or {}
    annual = _premium_over(list(range(y0, n)), "trailing 12 months") or {}
    # carried, not shown: lets a reviewer see exactly what the winter
    # restriction changed without re-running the engine on the old basis
    premium["annual_diagnostic"] = {
        r: annual[r]["premium_pct"] for r in ROUTES if r in annual}
    premium["window"] = WINTER_LABEL
    premium["hours"] = len(w_idx)
    premium["note"] = (
        "The coincidence premium: each route's replacement electricity "
        "priced at the hour it is drawn (Elexon MID) vs the same energy "
        "at the flat mean price, over " + WINTER_LABEL + " only (" +
        str(len(w_idx)) + " hours of the trailing year). Cold-evening "
        "concentration makes air-source dearest per unit; source "
        "temperature flattens the draw. The comparator is the mean price "
        "of the same months, so this is timing within the heating season "
        "- the winter-vs-summer price level is excluded as a seasonal "
        "effect, not a coincidence. Computed over the full year the "
        "figure is diluted by summer hours in which almost no heat is "
        "drawn, and can go negative. Wholesale basis - sits beside the "
        "spark gap.")

    return {
        "schema": 2,
        "basis": store["basis"],
        "system": sysv,
        "calibration": store["calibration"],
        "hourly_live_7d": hourly_live,
        "worst_week": worst_week,
        "binding_week": binding_week,
        "daily_90": daily,
        "heat_price": heat_price_series(store, sap_series=sap_series,
                                        retail_series=retail_series),
        "monthly_13": monthly,
        "monthly_calendar": monthly_calendar,
        "stress": stress,
        "stress_summer": stress_summer,
        "diurnal": diurnal_slope(store),
        "night_elbow": night_elbow(store),
        # Withdrawn from the page 9 Aug 2026 - hard to read at a glance and
        # not carrying weight in the argument. Still computed and logged
        # every run so the question stays answered and the panel can be
        # restored without redoing the work; the front end no longer reads
        # this key, and the methodology no longer documents it.
        "coincidence_premium_diagnostic": premium,
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


def _elec_limits(store, D, W, S, disp, gross, winter, _iso):
    """How far could the country electrify its heat before the winter
    peak fills the dispatchable block?

    For each route, bisect on the what-if share until the highest
    winter-hour call on dispatchable supply meets DISPATCH_DERATED_GW.
    The share is expressed as a percentage of national building heat and
    as the heat itself; above 100% means the whole of it fits.

    Also reported under progressively stiller winters, because the
    observed answer depends on the wind that actually blew: capping
    every winter hour at 5 GW, at the winter's own stillest hour, and at
    zero. The ORDERING of the three routes is invariant to all of them -
    they share one ceiling - which is the point worth making."""
    cal = store["calibration"]
    heat_annual = (cal["space_annual_TWh"] + cal["dhw_annual_TWh"])
    _t0 = dt.datetime.fromisoformat(store["start_day"])

    def _block_at(i):
        """The de-rated dispatchable block for the winter hour i falls in."""
        return dispatch_block(_t0 + dt.timedelta(hours=i))

    # per-unit-share series: gross/R_SHIFT is the 100%-share draw
    unit = {r: [g / R_SHIFT for g in gross[r]] for r in ROUTES}
    unit_disp = [x / R_SHIFT for x in disp]

    # Each hour is measured against ITS OWN winter's block, so the binding
    # hour is the one with the LEAST HEADROOM rather than the highest absolute
    # requirement. With one winter in the store the two are the same hour;
    # with two they need not be, and the headroom reading is the correct one.
    def peak(route, share, wind):
        best, bi = -1e9, winter[0]
        u, ud = unit[route], unit_disp
        for i in winter:
            v = (D[i] + max(0.0, share * u[i] - share * ud[i])
                 - wind(i) - S[i])
            over = v - _block_at(i)
            if over > best:
                best, bi = over, i
        # return the REQUIREMENT at the binding hour, and the hour itself
        i = bi
        req = (D[i] + max(0.0, share * u[i] - share * ud[i])
               - wind(i) - S[i])
        return req, bi

    def solve(route, wind):
        lo, hi = 0.0, 4.0
        for _ in range(42):
            mid = (lo + hi) / 2
            _r, _i = peak(route, mid, wind)
            if _r < _block_at(_i):
                lo = mid
            else:
                hi = mid
        share = (lo + hi) / 2
        _, bi = peak(route, share, wind)
        return share, bi

    still = min(W[i] for i in winter) if winter else 0.0
    scenarios = [
        ("observed", "the wind that actually blew", lambda i: W[i]),
        ("cap5", "every winter hour capped at 5 GW of wind",
         lambda i: min(W[i], 5.0)),
        ("still", "capped at %.1f GW - the winter's own stillest hour"
         % still, lambda i: min(W[i], still)),
        ("zero", "no wind at all, all winter", lambda i: 0.0),
    ]
    out = {"heat_annual_TWh": round(heat_annual, 0),
           "block_GW": DISPATCH_DERATED_GW, "scenarios": [], "routes": {}}
    for key, label, wind in scenarios:
        row = {"key": key, "label": label, "shares": {}}
        for r in ROUTES:
            share, bi = solve(r, wind)
            row["shares"][r] = round(100 * share, 1)
            if key == "observed":
                out["routes"][r] = {
                    "share_pct": round(100 * share, 1),
                    "heat_TWh": round(share * heat_annual, 0),
                    "limiting_hour": _iso(bi),
                    "all_of_it": share >= 1.0,
                }
        out["scenarios"].append(row)
    o = out["routes"]
    out["ratio_vs_air"] = (round(o["network"]["share_pct"]
                                 / o["ashp"]["share_pct"], 2)
                           if o["ashp"]["share_pct"] else None)
    out["note"] = (
        "A PEAK-CAPACITY test, not an energy test: it asks how much heat "
        "can be electrified before the winter peak fills today's "
        "dispatchable block (dagger), not whether the year's energy could "
        "be generated. Wind enters as observed, so the answer moves with "
        "the weather that actually occurred; the stiller scenarios show "
        "by how much. Distribution networks are unmodelled, no new "
        "capacity, storage or flexibility is credited, and one winter is "
        "one sample.")
    return out


def diurnal_slope(store):
    """Demand response to outdoor temperature, hour by hour, in summer.

    The separation the cooling tiers rest on. Comfort cooling is a DAYTIME
    load: it peaks with the afternoon and is near-absent at 4am. Process
    cooling runs all night, and its compressors still work harder as the air
    warms. So the slope of demand against temperature, taken hour by hour
    through the summer, splits the two without needing to know either level -
    the overnight floor is process, the daytime excess above it is comfort.

    Demeaned within (year, month, weekday-class, hour) before fitting, so a
    hot month that is also a quiet holiday month cannot masquerade as a
    temperature response. Solar is added back, matching the rest of retro.

    Publishes the profile only. The LEVEL cannot be recovered this way: below
    about 15 C the response goes flat, because refrigeration plant floats head
    pressure down to a minimum condensing temperature and data-centre
    economisers switch to free cooling. That elbow is real and replicates
    across both summers on record, and it is why no slope-to-level
    extrapolation appears here.
    """
    T = store.get("temp_C") or []
    D = store.get("demand_GW") or []
    S = store.get("solar_GW") or []
    if not T or not D:
        return None
    t0 = dt.datetime.fromisoformat(store["start_day"])
    rows = []
    for i in range(min(len(T), len(D))):
        if T[i] is None or D[i] is None:
            continue
        ts = t0 + dt.timedelta(hours=i)
        if ts.month not in (6, 7, 8):
            continue
        # demand_GW is ALREADY underlying: NESO ND plus embedded wind AND
        # solar (see fetch_hourly_system). Adding solar here counted it twice
        # and manufactured a correlation with temperature in daylight hours -
        # which is exactly where a cooling signal would appear. Found in peer
        # review, 17 Aug 2026. Do not "add embedded generation back": it is
        # already in the series.
        rows.append((ts, T[i], D[i]))
    if len(rows) < 400:
        return None

    def fit(sel):
        cells = {}
        for r in sel:
            cells.setdefault((r[0].year, r[0].month,
                              r[0].weekday() >= 5, r[0].hour), []).append(r)
        xs, ys = [], []
        for g in cells.values():
            if len(g) < 5:
                continue
            mt = sum(x[1] for x in g) / len(g)
            md = sum(x[2] for x in g) / len(g)
            for x in g:
                xs.append(x[1] - mt)
                ys.append(x[2] - md)
        n = len(xs)
        if n < 20:
            return None
        sxx = sum(x * x for x in xs)
        if sxx <= 0:
            return None
        b = sum(x * y for x, y in zip(xs, ys)) / sxx
        ss = sum(y * y for y in ys)
        rs = sum((y - b * x) ** 2 for x, y in zip(xs, ys))
        return {"slope_GW_per_C": round(b, 3),
                "r2": round(1 - rs / ss, 2) if ss else 0.0, "n": n}

    prof = []
    for h in range(24):
        g = fit([r for r in rows if r[0].hour == h])
        if g:
            g["hour"] = h
            prof.append(g)
    if len(prof) < 20:
        return None
    night = [p["slope_GW_per_C"] for p in prof if p["hour"] <= 5 or p["hour"] >= 22]
    day = [p["slope_GW_per_C"] for p in prof if 11 <= p["hour"] <= 15]
    floor = sum(night) / len(night) if night else 0.0
    peak = max(day) if day else 0.0
    return {
        "profile": prof,
        "night_floor_GW_per_C": round(floor, 3),
        "day_peak_GW_per_C": round(peak, 3),
        "comfort_excess_GW_per_C": round(peak - floor, 3),
        "summer_hours": len(rows),
        "note": ("Summer weekday and weekend demand regressed on outdoor air "
                 "temperature, separately for each hour of the day, demeaned "
                 "within year, month, day type and hour. The overnight floor "
                 "is cooling that runs regardless of the clock - refrigeration "
                 "and process plant working harder as the air warms. The "
                 "daytime excess above it is comfort cooling. Level is NOT "
                 "inferred from this: below about 15 C the response goes flat "
                 "as plant plausibly reaches its minimum condensing "
                 "temperature and data centres switch to free cooling. That "
                 "reading belongs to the NIGHT data only - the daily response "
                 "curve is convex for a different reason, the extensive "
                 "margin, which survives dividing out the COP. So the slope "
                 "cannot be "
                 "extrapolated back to a level."),
    }


def night_elbow(store):
    """Summer-night demand against outdoor temperature, binned, by year.

    The companion to diurnal_slope. Taking only the night hours removes
    comfort cooling, so what is left is the process fleet responding to
    ambient. The response is NOT linear: below about 15 C it is flat, because
    refrigeration plant floats head pressure down only to a minimum condensing
    temperature and data-centre economisers switch to free cooling. Above it
    the slope is steep.

    Published per year so the replication is visible rather than asserted -
    the elbow appears independently in each summer on record. The same
    convexity is already present in the CDD response curve that produces tiers
    1 and 2, arrived at from a different variable and a different aggregation,
    which is why the two charts sit together.

    A level is NOT derivable from this. The flat segment is a floor whose
    height is exactly the quantity aggregate demand cannot see.
    """
    T = store.get("temp_C") or []
    D = store.get("demand_GW") or []
    S = store.get("solar_GW") or []
    if not T or not D:
        return None
    t0 = dt.datetime.fromisoformat(store["start_day"])
    rows = []
    for i in range(min(len(T), len(D))):
        if T[i] is None or D[i] is None:
            continue
        ts = t0 + dt.timedelta(hours=i)
        if ts.month not in (5, 6, 7, 8, 9) or not (1 <= ts.hour <= 5):
            continue
        # demand_GW is ALREADY underlying: NESO ND plus embedded wind AND
        # solar (see fetch_hourly_system). Adding solar here counted it twice
        # and manufactured a correlation with temperature in daylight hours -
        # which is exactly where a cooling signal would appear. Found in peer
        # review, 17 Aug 2026. Do not "add embedded generation back": it is
        # already in the series.
        rows.append((ts, T[i], D[i]))
    if len(rows) < 200:
        return None

    def prep(sel):
        cells = {}
        for r in sel:
            cells.setdefault((r[0].year, r[0].month,
                              r[0].weekday() >= 5, r[0].hour), []).append(r)
        gm = sum(r[2] for r in sel) / len(sel)
        out = []
        for g in cells.values():
            if len(g) < 5:
                continue
            md = sum(x[2] for x in g) / len(g)
            for x in g:
                out.append((x[1], x[2] - md + gm))
        return out

    def seg(pts, lo, hi):
        sel = [p for p in pts if lo <= p[0] < hi]
        if len(sel) < 30:
            return None
        mx = sum(p[0] for p in sel) / len(sel)
        my = sum(p[1] for p in sel) / len(sel)
        sxx = sum((p[0] - mx) ** 2 for p in sel)
        if sxx <= 0:
            return None
        b = sum((p[0] - mx) * (p[1] - my) for p in sel) / sxx
        return {"slope_GW_per_C": round(b, 3), "n": len(sel)}

    BREAK = 15.0
    years = []
    for yr in sorted({r[0].year for r in rows}):
        sel = [r for r in rows if r[0].year == yr]
        if len(sel) < 120:
            continue
        pts = prep(sel)
        bins = {}
        for t, v in pts:
            bins.setdefault(int(round(t)), []).append(v)
        series = [{"t_C": k, "demand_GW": round(sum(v) / len(v), 2), "n": len(v)}
                  for k, v in sorted(bins.items()) if len(v) >= 5]
        years.append({"year": yr, "bins": series,
                      "below": seg(pts, -5, BREAK), "above": seg(pts, BREAK, 40),
                      "hours": len(sel)})
    if not years:
        return None
    allpts = prep(rows)
    return {
        "break_C": BREAK,
        "years": years,
        "below": seg(allpts, -5, BREAK),
        "above": seg(allpts, BREAK, 40),
        "note": ("Summer-night demand, 01:00-05:00, May to September, demeaned "
                 "within year, month, day type and hour. Night hours carry no "
                 "comfort cooling, so the response is the process fleet meeting "
                 "a warmer condenser. It is not a straight line: below about "
                 "15 degC it is flat, because plant floats head pressure only "
                 "to a minimum condensing temperature and data centres switch "
                 "to free cooling. The elbow appears independently in each "
                 "summer on record. No level can be read off it - the flat "
                 "segment is a floor whose height aggregate demand cannot see."),
    }


def heat_price_series(store, sap_series=None, retail_series=None,
                      eff_gas=0.835, eff_gas_dhw=0.73):
    """Daily commodity cost of DELIVERED heat, by route, on both bases.

    COP IS NOT A CONSTANT. Until 12 Aug 2026 this divided one electricity
    price by three fixed seasonal factors, which made the three electric lines
    parallel by construction and hid the effect the rest of the site exists to
    show: an air-source machine loses efficiency exactly when heat is wanted,
    while a ground loop barely moves. The daily COP now comes from route_cop()
    on that day's mean temperature, using the SAME calibrated etas as the
    hourly engine - one COP model on the site, not two.

    The store carries hourly electricity price and temperature, so both sides
    of each division are measured. Two electricity means are published per
    day: the flat mean, which the ticker uses, and the HEAT-WEIGHTED mean,
    which is what a heat pump would actually pay.

    Gas is not in the store - only today's SAP is fetched by the ticker - so
    the gas series is carried only where a caller supplies one.
    """
    M = store.get("mid_gbp_mwh") or []
    T = store.get("temp_C") or []
    H = store.get("heat_GWh") or []
    if not M:
        return None
    etas = (store.get("calibration") or {}).get("eta") or {}
    if not etas:
        return None
    t0 = dt.date.fromisoformat(store["start_day"])
    flat, wtd, temps = {}, {}, {}
    for i, v in enumerate(M):
        if v is None:
            continue
        d_ = (t0 + dt.timedelta(days=i // 24)).isoformat()
        h = H[i] if i < len(H) and H[i] else 0.0
        flat.setdefault(d_, []).append(v)
        wtd.setdefault(d_, []).append((v, h))
        if i < len(T) and T[i] is not None:
            temps.setdefault(d_, []).append(T[i])
    ROUTE_KEYS = ("ashp", "shallow", "network")
    out = {"dates": [], "elec_flat_gbp_mwh": [], "elec_heat_wtd_gbp_mwh": [],
           "temp_C": [], "routes": {k: [] for k in ROUTE_KEYS},
           "cop": {k: [] for k in ROUTE_KEYS},
           "cop_dhw": {k: [] for k in ROUTE_KEYS},
           "dhw": {k: [] for k in ROUTE_KEYS},
           "blend": {k: [] for k in ROUTE_KEYS},
           "space_share": [], "space_GWh": [], "dhw_GWh": [],
           "eff_gas": eff_gas, "eff_gas_dhw": eff_gas_dhw}
    for d_ in sorted(flat):
        vals = flat[d_]
        if len(vals) < 20 or d_ not in temps:
            continue                        # a part day is not a price
        fm = sum(vals) / len(vals)
        pw = wtd[d_]
        hsum = sum(h for _, h in pw)
        wm = (sum(p * h for p, h in pw) / hsum) if hsum > 0 else fm
        tm = sum(temps[d_]) / len(temps[d_])
        out["dates"].append(d_)
        out["elec_flat_gbp_mwh"].append(round(fm, 2))
        out["elec_heat_wtd_gbp_mwh"].append(round(wm, 2))
        out["temp_C"].append(round(tm, 2))
        for k in ROUTE_KEYS:
            c = route_cop(k, tm, etas.get(k, 0.33))
            out["cop"][k].append(round(c, 2))
            out["routes"][k].append(round(fm / c, 2))
            # HOT WATER on the same machine. The cylinder needs its own flow
            # temperature whatever the weather, so none of the flow-reduction
            # benefit is available and the lift never shrinks. Same engine,
            # same eta, one argument different.
            ch = route_cop(k, tm, etas.get(k, 0.33), t_flow=DHW_FLOW_C)
            out["cop_dhw"][k].append(round(ch, 2))
            out["dhw"][k].append(round(fm / ch, 2))

    # AS DELIVERED: the two services blended by the mix actually being served
    # that day. Space heat is HDD-shaped with the same summer damping the
    # hourly engine uses; hot water is flat. The blend is a HARMONIC mean
    # weighted by delivered service, because what is being averaged is a cost
    # per unit delivered:
    #
    #     effective COP = 1 / (w_space/COP_space + w_dhw/COP_dhw)
    #
    # This is the only one of the three that moves with the load mix, and it
    # is where the separate hot-water efficiency becomes visible: cheapest per
    # MWh in January when the load is mostly space heating, dearest in July
    # when it is almost all hot water.
    cal = store.get("calibration") or {}
    sp_ann = cal.get("space_annual_TWh") or 0.0
    dh_ann = cal.get("dhw_annual_TWh") or 0.0
    if sp_ann and dh_ann and out["dates"]:
        base_c = cal.get("base_temp_C", 15.5)

        def _damp(hd):
            if hd <= SUMMER_HDD_OFF:
                return 0.0
            if hd >= SUMMER_HDD_FULL:
                return 1.0
            x = (hd - SUMMER_HDD_OFF) / (SUMMER_HDD_FULL - SUMMER_HDD_OFF)
            return x * x * (3 - 2 * x)                    # smoothstep

        hdd = [max(0.0, base_c - t) for t in out["temp_C"]]
        wgt = [h * _damp(h) for h in hdd]
        den = sum(wgt[-365:]) or (sum(wgt) or 1.0)
        dhw_day = dh_ann * 1000.0 / 365.0                # GWh/day, flat
        for i in range(len(out["dates"])):
            sp = sp_ann * 1000.0 * wgt[i] / den
            tot = sp + dhw_day
            ws = (sp / tot) if tot else 0.0
            out["space_share"].append(round(ws, 4))
            # The VOLUMES, not just the share. A price for a service nobody is
            # buying in July is a different claim from one they are, and the
            # reader cannot weigh the three service views without seeing how
            # much of each is actually being delivered on the same window.
            out["space_GWh"].append(round(sp, 1))
            out["dhw_GWh"].append(round(dhw_day, 1))
            for k in ROUTE_KEYS:
                cs, cd = out["cop"][k][i], out["cop_dhw"][k][i]
                inv = (ws / cs if cs else 0.0) + ((1 - ws) / cd if cd else 0.0)
                out["blend"][k].append(
                    round(out["elec_flat_gbp_mwh"][i] * inv, 2) if inv else None)

    # GAS, where a series exists. Aligned to the SAME dates as the electric
    # routes and left as None on days the price was not published, so a gap in
    # the feed shows as a gap in the line rather than a straight segment
    # across it. SAP is p/kWh; x10 puts it on the GBP/MWh axis the others use.
    # Restored 12 Aug 2026 alongside the retail join - the same rewrite of
    # this function removed both.
    if sap_series and sap_series.get("dates"):
        by = dict(zip(sap_series["dates"], sap_series["p_per_kwh"]))
        out["routes"]["gas"] = [
            (round(by[d_] * 10.0 / eff_gas, 2) if d_ in by else None)
            for d_ in out["dates"]]
        # Gas hot water at the SAP-derived summer efficiency, and the blend
        # at the same harmonic mean the electric routes use.
        out["dhw"]["gas"] = [
            (round(by[d_] * 10.0 / eff_gas_dhw, 2) if d_ in by else None)
            for d_ in out["dates"]]
        if out["space_share"]:
            out["blend"]["gas"] = []
            for i, d_ in enumerate(out["dates"]):
                if d_ not in by:
                    out["blend"]["gas"].append(None)
                    continue
                ws = out["space_share"][i]
                inv = ws / eff_gas + (1 - ws) / eff_gas_dhw
                out["blend"]["gas"].append(round(by[d_] * 10.0 * inv, 2))
        out["gas_days"] = sum(1 for v in out["routes"]["gas"] if v is not None)
        out["gas_publication"] = sap_series.get("publication")

    # RETAIL, aligned to the same dates. The cap is a step function, so this
    # is flat between quarters by design rather than by failure. Restored
    # 12 Aug 2026 after a rewrite of this function silently deleted it: the
    # replacement spanned from the signature to the note and took this with
    # it, which is why the basis toggle vanished from the page.
    if retail_series and retail_series.get("dates"):
        # THREE SERVICES ON THIS BASIS TOO, from 14 Aug 2026. Retail was built
        # before the service split and carried space heating only, so the
        # service toggle silently did nothing whenever "at the meter" was
        # selected - three buttons that appeared to work and did not.
        idx = {d_: i for i, d_ in enumerate(retail_series["dates"])}
        out["retail"] = {}
        out["retail_dhw"] = {}
        out["retail_blend"] = {}
        ss = out["space_share"]
        for k in ("gas", "ashp", "shallow", "network"):
            src = retail_series.get(k) or []
            unit = [(src[idx[d_]] if d_ in idx and idx[d_] < len(src) else None)
                    for d_ in out["dates"]]
            if k == "gas":
                # Gas arrives ALREADY divided by the space efficiency. Recover
                # the raw unit price, then redivide per service.
                raw = [(v * eff_gas if v is not None else None) for v in unit]
                out["retail"][k] = unit
                out["retail_dhw"][k] = [
                    (round(v / eff_gas_dhw, 2) if v is not None else None)
                    for v in raw]
                out["retail_blend"][k] = [
                    (round(v * (ss[i] / eff_gas
                                + (1 - ss[i]) / eff_gas_dhw), 2)
                     if v is not None and i < len(ss) else None)
                    for i, v in enumerate(raw)]
                continue
            # Electric routes arrive as UNIT prices; divide by that day's own
            # COP for each service, so retail and wholesale share one
            # efficiency model throughout.
            out["retail"][k] = [
                (round(v / c, 2) if v is not None and c else None)
                for v, c in zip(unit, out["cop"][k])]
            out["retail_dhw"][k] = [
                (round(v / c, 2) if v is not None and c else None)
                for v, c in zip(unit, out["cop_dhw"][k])]
            blend = []
            for i, v in enumerate(unit):
                if v is None or i >= len(ss):
                    blend.append(None)
                    continue
                cs, cd = out["cop"][k][i], out["cop_dhw"][k][i]
                inv = (ss[i] / cs if cs else 0.0) + \
                      ((1 - ss[i]) / cd if cd else 0.0)
                blend.append(round(v * inv, 2) if inv else None)
            out["retail_blend"][k] = blend
        out["retail_note"] = (
            "Domestic routes at the Ofgem cap in force on each date - a step "
            "function of policy decisions, not a market series. The "
            "geothermal network is priced at the NON-DOMESTIC electricity "
            "rate, because a network operator buys on a commercial contract "
            "rather than the domestic cap, and it is the operator's input "
            "cost that is being compared - not what a connected household is "
            "billed, which is a network tariff this model does not carry.")

    # HEAT-WEIGHTED SUMMARY. An unweighted daily chart gives a mild June day
    # the same weight as 5 January, which flatters air-source badly: on point
    # COP it beats ground source in mild weather and only loses in the cold,
    # but nearly all the heat is drawn in the cold. Unweighted, air-source
    # averages ABOVE ground source and inverts the site's own SPF ordering.
    # Published so the chart can say what the annual picture is rather than
    # letting the eye average the days.
    hd = {}
    for i, v in enumerate(H or []):
        if v:
            k_ = (t0 + dt.timedelta(days=i // 24)).isoformat()
            hd[k_] = hd.get(k_, 0.0) + v
    hw = sum(hd.get(d_, 0.0) for d_ in out["dates"])
    if hw > 0:
        out["cop_heat_weighted"] = {
            k: round(sum(out["cop"][k][i] * hd.get(d_, 0.0)
                         for i, d_ in enumerate(out["dates"])) / hw, 2)
            for k in ROUTE_KEYS}
        out["cop_unweighted"] = {
            k: round(sum(out["cop"][k]) / len(out["cop"][k]), 2)
            for k in ROUTE_KEYS}
    out["note"] = (
        "Commodity cost of DELIVERED heat: the daily electricity index "
        "divided by each route's seasonal factor. The flat mean is what the "
        "headline ticker uses; the heat-weighted mean is what a heat pump "
        "would actually pay, because its load sits on the morning and evening "
        "peaks. The difference is the coincidence premium - about 3% across a "
        "winter - so the flat basis understates every heat-pump route by "
        "roughly that much. Gas is not in this series: only today's SAP is "
        "fetched for the ticker; the line here uses a daily SAP series from "
        "the same National Gas publication API. SAP is a BALANCING price - "
        "what National Gas pays to balance the system on the day - which "
        "tracks NBP day-ahead closely but is not the same instrument.")
    return out


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
            "cannot cover. TWO winters on record from 12 Aug 2026, so "
            "the binding hour is now chosen across them rather than "
            "describing the only one available - and 2025-26 still "
            "holds it, tested against 2024-25's own lower ceiling. "
            "(dagger): 2025-26's cold "
            "snap was windy; its still spell (18-21 Mar, wind to 1.6 "
            "GW) was mild; a year where they coincide is the risk case "
            "this chart lets the reader construct."),
        "routes": {},
        "limits": _elec_limits(store, D, W, S, disp, gross, winter, _iso),
        "basis_short": (
            "Heat demand is modelled from weather (dagger); prices, "
            "carbon, demand, wind and solar are measured. Cooling shown "
            "here is already inside today's demand, not added to it. "
            "The capacity line is a fixed generation block (dagger - "
            "NESO Winter Outlook, per winter: 60.6 GW for 2024/25 and "
            "62.3 for 2025/26) plus the wind and solar "
            "actually generated: a simple overlay, not a model of how "
            "the grid would really respond. Two winters on record, "
            "each tested against its own fleet. Full "
            "method and every assumption: see the methodology."),
    }

    _t0v = dt.datetime.fromisoformat(store["start_day"])
    def _blk(i):
        return dispatch_block(_t0v + dt.timedelta(hours=i))

    for r in ROUTES:
        # least headroom, not highest requirement - the ceiling now differs
        # by winter, so the two are not the same hour once the store holds
        # more than one.
        ib = max(winter, key=lambda i: D[i] + net[r][i] - W[i] - S[i] - _blk(i))
        req = D[ib] + net[r][ib] - W[ib] - S[ib]
        exceed = sum(1 for i in winter
                     if D[i] + net[r][i] - W[i] - S[i] > _blk(i))
        out["routes"][r] = {
            "binding_hour": _iso(ib),
            "binding_temp_C": store["temp_C"][ib],
            "binding_demand_GW": round(D[ib], 1),
            "binding_wind_GW": round(W[ib], 1),
            "binding_solar_GW": round(S[ib], 2),
            "net_add_GW": round(net[r][ib], 1),
            "gross_add_GW": round(gross[r][ib], 1),
            "dispatch_req_GW": round(req, 1),
            "block_GW": _blk(ib),
            "headroom_GW": round(_blk(ib) - req, 1),
            "hours_above_block": exceed,
        }
    return out
