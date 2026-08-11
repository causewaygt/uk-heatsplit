"""Fetch daily mean dewpoint from Open-Meteo (ERA5) on the same population
weights as fetch_degree_days, and derive moist-air enthalpy days.

WHY DEWPOINT AND NOT RELATIVE HUMIDITY
Latent cooling load is the work of condensing water out of the air, so what
matters is ABSOLUTE moisture. Relative humidity falls as air warms at constant
moisture content, so it moves the wrong way on exactly the days that carry the
load. Dewpoint is a direct proxy for absolute moisture and Open-Meteo serves it
natively from the same ERA5 reanalysis the temperature series comes from.

WHY ENTHALPY DAYS
Cooling plant removes total heat, sensible plus latent. Degree days count only
the sensible part. Enthalpy days integrate the moist-air enthalpy above the
enthalpy of air at the comfort setpoint, so one variable carries both. This is
the standard construction in the cooling-load literature and it is the natural
test of whether the convexity we measure in the demand response is latent load
or the extensive margin of buildings switching on.

NOTE ON THE REQUEST SHAPE
The Open-Meteo daily endpoint does not aggregate humidity, so this fetches
HOURLY dewpoint and means it per day - 24x the rows of the degree-day call.
Requests are chunked by date to stay inside the response cap.

Attribution: Open-Meteo.com (CC BY 4.0) / ERA5 (Copernicus).
"""

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
import time

import requests

# Same population weights and endpoint as the degree-day series - imported
# rather than copied, because the whole value of this series is that it is on
# IDENTICAL weights to the temperature it will be regressed alongside.
# Searched rather than assumed, so this runs from analysis/, from scripts/, or
# from the repo root without editing.
_HERE = pathlib.Path(__file__).resolve().parent
for _cand in (_HERE, _HERE.parent / "scripts", _HERE / "scripts",
              _HERE.parent, _HERE.parent.parent / "scripts"):
    if (_cand / "fetch_degree_days.py").is_file():
        sys.path.insert(0, str(_cand))
        break
else:
    raise SystemExit(
        "fetch_humidity: cannot find fetch_degree_days.py. Looked in:\n  "
        + "\n  ".join(str(c) for c in
                       (_HERE, _HERE.parent / "scripts", _HERE / "scripts",
                        _HERE.parent, _HERE.parent.parent / "scripts"))
        + "\nPut this file in analysis/ with fetch_degree_days.py in scripts/.")

from fetch_degree_days import GB_POINTS, ARCHIVE_URL   # noqa: E402

# Comfort reference for enthalpy days: 22 degC dry bulb at 50% RH is the
# conventional UK non-domestic summer design condition. Cooling plant works
# to bring outdoor air to about this state, so enthalpy above it is the load.
REF_T_C = 22.0
REF_RH = 0.50

DAY_CHUNK = 180          # days per request; hourly rows are 24x the daily call
PRESSURE_KPA = 101.325


def _svp_kpa(t_c):
    """Saturation vapour pressure over water, Tetens. kPa."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def humidity_ratio(t_dew_c):
    """Mass of water per mass of dry air, from dewpoint alone.

    At the dewpoint the air is saturated, so the actual vapour pressure equals
    the saturation vapour pressure AT THE DEWPOINT - which is why dewpoint is
    sufficient and temperature is not needed here.
    """
    pv = _svp_kpa(t_dew_c)
    return 0.622 * pv / max(1e-6, PRESSURE_KPA - pv)


def enthalpy_kj_per_kg(t_c, t_dew_c):
    """Moist-air specific enthalpy, kJ per kg of dry air."""
    w = humidity_ratio(min(t_dew_c, t_c))      # dewpoint cannot exceed dry bulb
    return 1.006 * t_c + w * (2501.0 + 1.86 * t_c)


def _ref_enthalpy():
    pv = REF_RH * _svp_kpa(REF_T_C)
    w = 0.622 * pv / (PRESSURE_KPA - pv)
    return 1.006 * REF_T_C + w * (2501.0 + 1.86 * REF_T_C)


REF_ENTHALPY = _ref_enthalpy()


def _fetch_hourly(start, end, retries=4):
    pts = GB_POINTS
    params = {
        "latitude": ",".join(str(p[1]) for p in pts),
        "longitude": ",".join(str(p[2]) for p in pts),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": "dewpoint_2m,temperature_2m", "timezone": "UTC",
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=180)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data]
        except Exception as e:
            last = e
            wait = 15 * (attempt + 1)
            print(f"open-meteo humidity attempt {attempt+1} failed ({e}); "
                  f"retry in {wait}s")
            time.sleep(wait)
    raise last


def fetch_humidity(days=400):
    """Population-weighted daily mean dewpoint and enthalpy days for GB."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)

    spans, w0 = [], start
    while w0 <= end:
        w1 = min(end, w0 + dt.timedelta(days=DAY_CHUNK - 1))
        spans.append((w0, w1))
        w0 = w1 + dt.timedelta(days=1)
    if len(spans) > 1:
        print(f"humidity: {days} days split into {len(spans)} windows "
              f"of up to {DAY_CHUNK} days")

    tot_w = sum(p[3] for p in GB_POINTS)
    dew_num, temp_num, enth_num, wsum = {}, {}, {}, {}

    for s, e in spans:
        results = _fetch_hourly(s, e)
        for (name, lat, lon, w), res in zip(GB_POINTS, results):
            h = res["hourly"]
            for stamp, td, ta in zip(h["time"], h["dewpoint_2m"],
                                     h["temperature_2m"]):
                if td is None or ta is None:
                    continue
                d_ = stamp[:10]
                dew_num[d_] = dew_num.get(d_, 0.0) + td * w
                temp_num[d_] = temp_num.get(d_, 0.0) + ta * w
                enth_num[d_] = (enth_num.get(d_, 0.0)
                                + enthalpy_kj_per_kg(ta, td) * w)
                wsum[d_] = wsum.get(d_, 0.0) + w

    # a full day is 24 hours x the full weight set
    full = 24.0 * tot_w
    dates = sorted(d_ for d_ in wsum if wsum[d_] >= 0.9 * full)
    out = {"dates": dates,
           "dewpoint_mean_C": [], "temp_mean_C": [],
           "enthalpy_kj_per_kg": [], "enthalpy_days": []}
    for d_ in dates:
        n = wsum[d_]
        dew = dew_num[d_] / n
        tmp = temp_num[d_] / n
        ent = enth_num[d_] / n
        out["dewpoint_mean_C"].append(round(dew, 2))
        out["temp_mean_C"].append(round(tmp, 2))
        out["enthalpy_kj_per_kg"].append(round(ent, 2))
        # enthalpy days: excess total heat above the comfort reference state
        out["enthalpy_days"].append(round(max(0.0, ent - REF_ENTHALPY), 3))
    out["reference"] = {"t_C": REF_T_C, "rh": REF_RH,
                        "enthalpy_kj_per_kg": round(REF_ENTHALPY, 2)}
    return out


def _selftest():
    print("reference state %.1f degC at %d%% RH = %.2f kJ/kg"
          % (REF_T_C, int(REF_RH * 100), REF_ENTHALPY))
    print("  %-30s %9s %9s %11s" % ("condition", "w g/kg", "h kJ/kg",
                                    "enth days"))
    for t, td, lab in ((15, 12, "mild, damp"), (20, 14, "warm, moderate"),
                       (25, 17, "hot, humid"), (30, 20, "very hot, humid"),
                       (30, 12, "very hot, DRY")):
        print("  %-30s %9.2f %9.2f %11.2f"
              % (lab, humidity_ratio(td) * 1000, enthalpy_kj_per_kg(t, td),
                 max(0.0, enthalpy_kj_per_kg(t, td) - REF_ENTHALPY)))
    gap = enthalpy_kj_per_kg(30, 20) - enthalpy_kj_per_kg(30, 12)
    print("  the two 30 degC rows are %.1f kJ/kg apart on the same dry bulb - "
          "degree days cannot tell them apart." % gap)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=400,
                    help="days of history to fetch (default 400)")
    ap.add_argument("--out", default="humidity.json",
                    help="where to write the series (default humidity.json)")
    ap.add_argument("--selftest", action="store_true",
                    help="print the psychrometric check and exit, no network")
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        sys.exit(0)

    d = fetch_humidity(days=a.days)
    with open(a.out, "w") as fh:
        json.dump(d, fh)
    print("wrote %s: %d days, %s to %s"
          % (a.out, len(d["dates"]), d["dates"][0], d["dates"][-1]))
    print("  latest dewpoint %.1f degC, enthalpy days %.2f"
          % (d["dewpoint_mean_C"][-1], d["enthalpy_days"][-1]))
