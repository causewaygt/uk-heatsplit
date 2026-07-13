"""Overheating-degree-hours (ODH) above the CIBSE 26degC comfort threshold.

Fetches hourly 2m temperature for the population-weighted GB point set from
the Open-Meteo archive (same source as the daily degree-day feed), computes
population-weighted degree-hours above 26degC per day for a recent window.

ODH_day = sum over hours of max(0, T_hour - 26.0), weighted across points.

Optional feed: callers tolerate failure. CIBSE TM52/TM59 use 26degC as the
occupied/bedroom overheating threshold; all hours are counted here (an
occupied-hours weighting would need a diurnal occupancy model - noted as a
simplification where displayed).
"""

import datetime as dt
import time
import requests

# Same population-weighted set as fetch_degree_days
GB_POINTS = [
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

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
THRESHOLD_C = 26.0


def fetch_odh(days=14):
    """Population-weighted daily ODH26 (degC.h) for the trailing window.
    Returns {"daily": {date: odh}, "threshold_c": 26.0}."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    lats = ",".join(str(p[1]) for p in GB_POINTS)
    lons = ",".join(str(p[2]) for p in GB_POINTS)
    weights = [p[3] for p in GB_POINTS]
    total_w = sum(weights)

    last_err = None
    for attempt in range(4):
        try:
            r = requests.get(ARCHIVE_URL, params={
                "latitude": lats, "longitude": lons,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "hourly": "temperature_2m", "timezone": "UTC",
            }, timeout=90)
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:                     # runner throttling
            last_err = e
            time.sleep(8 * (attempt + 1))
    else:
        raise RuntimeError(f"Open-Meteo hourly fetch failed: {last_err}")

    blocks = payload if isinstance(payload, list) else [payload]
    if len(blocks) != len(GB_POINTS):
        raise RuntimeError(f"Expected {len(GB_POINTS)} hourly blocks, "
                           f"got {len(blocks)}")

    daily = {}
    for block, w in zip(blocks, weights):
        times = block["hourly"]["time"]
        temps = block["hourly"]["temperature_2m"]
        for t, v in zip(times, temps):
            if v is None:
                continue
            date = t[:10]
            exceed = max(0.0, v - THRESHOLD_C)
            if exceed > 0:
                daily[date] = daily.get(date, 0.0) + exceed * w
    out = {d: round(v / total_w, 2) for d, v in daily.items()}
    # include zero days for completeness of the window
    d_ = start
    while d_ <= end:
        out.setdefault(d_.isoformat(), 0.0)
        d_ += dt.timedelta(days=1)
    wk = sorted(out)[-7:]
    print(f"ODH26: week {wk[0]}..{wk[-1]} total "
          f"{sum(out[d] for d in wk):.1f} degC.h (pop-weighted)")
    return {"daily": out, "threshold_c": THRESHOLD_C}


if __name__ == "__main__":
    res = fetch_odh()
    for d in sorted(res["daily"])[-7:]:
        print(d, res["daily"][d])
