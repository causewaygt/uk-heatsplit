"""Stub hourly feeds for the retrospective engine. All series derive from
the SHARED per-date weather (stub_weather) plus deterministic per-hour
terms, so cross-feed regressions see one consistent world - the lesson
from the cooling-diagnostic stubs. Injected truth, for validation:
  temp: daily mean (shared) + diurnal cosine (amp 4C, warmest 15:00) + hour noise sd 0.8
  mid:  70 + 25*evening_peak + 0.8*(15-T) + noise sd 6   (GBP/MWh)
  ci:   90 + 60*winter + 25*evening - solar dip + noise sd 12 (g/kWh)
"""
import datetime as dt, math, random
from stub_weather import temp_for


def _rng(tag, hour_iso):
    return random.Random(tag + hour_iso)


def hourly_temp(start_day, n_days):
    out = []
    d0 = dt.date.fromisoformat(start_day)
    for i in range(n_days):
        d = d0 + dt.timedelta(days=i)
        base = temp_for(d.isoformat())
        for h in range(24):
            iso = d.isoformat() + "T%02d" % h
            diurnal = 4.0 * math.cos((h - 15) / 24.0 * 2 * math.pi)
            out.append(round(base + diurnal
                             + _rng("ht", iso).gauss(0, 0.8), 2))
    return out


def hourly_mid(start_day, n_days, temps):
    out = []
    d0 = dt.date.fromisoformat(start_day)
    k = 0
    for i in range(n_days):
        d = d0 + dt.timedelta(days=i)
        for h in range(24):
            iso = d.isoformat() + "T%02d" % h
            evening = math.exp(-((h - 18) / 2.5) ** 2)
            v = (70.0 + 25.0 * evening + 0.8 * max(0.0, 15.0 - temps[k])
                 + _rng("mid", iso).gauss(0, 6))
            out.append(round(max(5.0, v), 2))
            k += 1
    return out


def hourly_ci(start_day, n_days):
    out = []
    d0 = dt.date.fromisoformat(start_day)
    for i in range(n_days):
        d = d0 + dt.timedelta(days=i)
        doy = d.timetuple().tm_yday
        winter = 0.5 * (1 + math.cos((doy - 15) / 365.0 * 2 * math.pi))
        for h in range(24):
            iso = d.isoformat() + "T%02d" % h
            evening = math.exp(-((h - 18) / 3.0) ** 2)
            solar = math.exp(-((h - 13) / 3.0) ** 2) * (1 - winter)
            v = (90.0 + 60.0 * winter + 25.0 * evening - 35.0 * solar
                 + _rng("ci", iso).gauss(0, 12))
            out.append(round(max(20.0, v), 1))
    return out


def hourly_system(start_day, n_days, temps):
    """Stub system feeds with injected truth (GW, hourly):
      demand: 22 + 14*winter + 6*evening_bell + 0.6*max(0,15-T) + n(0,1.2)
              - underlying-demand convention (embedded generation included
              on both sides of the ledger)
      wind:   (8 + 8*season) * (1 - 0.55*coldness) + n(0,1.5), floor 0.3
              - the cold-still correlation IS the injected truth: stub
              Dunkelflautes exist by construction, tests assert them
      solar:  daylight bell * season_amp * (1 - cloud), zero at night
    Returns dict {demand_GW, wind_GW, solar_GW} lists of n_days*24."""
    import datetime as dt, math
    d0 = dt.date.fromisoformat(start_day)
    demand, wind, solar = [], [], []
    k = 0
    for i in range(n_days):
        d = d0 + dt.timedelta(days=i)
        doy = d.timetuple().tm_yday
        winter = 0.5 * (1 + math.cos((doy - 15) / 365.0 * 2 * math.pi))
        for h in range(24):
            iso = d.isoformat() + "T%02d" % h
            t = temps[k]; k += 1
            evening = math.exp(-((h - 18) / 2.5) ** 2)
            coldness = min(1.0, max(0.0, (10.0 - t) / 12.0))
            dem = (22.0 + 14.0 * winter + 6.0 * evening
                   + 0.6 * max(0.0, 15.0 - t)
                   + _rng("sd", iso).gauss(0, 1.2))
            w = ((8.0 + 8.0 * winter) * (1 - 0.55 * coldness)
                 + _rng("sw", iso).gauss(0, 1.5))
            daylight = max(0.0, math.cos((h - 13) / 7.0 * math.pi / 2))
            cloud = min(1.0, max(0.0, _rng("sc", iso).gauss(0.35, 0.25)))
            sv = 9.0 * (1.2 - winter) * daylight * (1 - cloud)
            demand.append(round(max(15.0, dem), 2))
            wind.append(round(max(0.3, w), 2))
            solar.append(round(max(0.0, sv), 2))
    return {"demand_GW": demand, "wind_GW": wind, "solar_GW": solar}
