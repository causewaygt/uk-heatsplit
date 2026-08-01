"""Shared deterministic per-date temperature for all stub feeds, so that
gas, electricity and degree-day stubs respond to the SAME weather - as the
real feeds do. Per-date seeding keeps every module's series identical
regardless of date-range or iteration order."""
import datetime as dt, math, random


def temp_for(date_iso):
    doy = dt.date.fromisoformat(date_iso).timetuple().tm_yday
    noise = random.Random("wx" + date_iso).gauss(0, 2)
    return 11.0 + 8.0 * math.cos((doy - 200) / 365.0 * 2 * math.pi) + noise
