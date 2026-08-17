"""Offline fit of the weather-driven cooling limb of GB daily demand.

WHAT THIS IS FOR
The site's cooling tiers came from a response curve that demeans demand within
(month, weekend class) before binning. That demeaning removes the LEVEL: it
kills the regression intercept, so the cooling balance point is unidentified
and the recovered figure is a lower bound - about 15% of the ECUK anchor.

This fits the baseline EXPLICITLY instead of removing it, on undemeaned daily
data. The intercept survives, the balance point becomes identifiable, and the
cooling limb comes out at roughly 70-75% of the ECUK anchor rather than 15%.

Each candidate term is reported SEPARATELY against the same base model, then
all together, so the marginal contribution of each is visible rather than
buried in a single R2.

    python3 fit_cooling.py retro.json [humidity.json]

The humidity file is optional and is the output of fetch_humidity.fetch_humidity().
Without it the dewpoint and enthalpy terms are skipped and reported as such.
"""

import datetime as dt
import json
import math
import sys

# The ECUK anchor this is tested against. Services cooling & ventilation
# electricity, split 54:46 ventilation:cooling from the BEES fans versus
# space-cooling lines. Ventilation is flat and is NOT part of the cooling limb.
ECUK_COOL_VENT_TWH = 10.4
COOL_SHARE = 0.46
ANCHOR_TWH = ECUK_COOL_VENT_TWH * COOL_SHARE

HDD_BASE = 15.5          # heating limb, conventional UK base
TC_SCAN = [11.0 + 0.5 * i for i in range(20)]

# UK bank holidays over the plausible window. Extend as the record grows.
BANK_HOLIDAYS = {
    (2024, 8, 26), (2024, 12, 25), (2024, 12, 26),
    (2025, 1, 1), (2025, 4, 18), (2025, 4, 21), (2025, 5, 5),
    (2025, 5, 26), (2025, 8, 25), (2025, 12, 25), (2025, 12, 26),
    (2026, 1, 1), (2026, 4, 3), (2026, 4, 6), (2026, 5, 4),
    (2026, 5, 25), (2026, 8, 31), (2026, 12, 25), (2026, 12, 28),
}


def ols(Y, X):
    n, k = len(Y), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)]
         + [sum(X[i][a] * Y[i] for i in range(n))] for a in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda q: abs(A[q][c]))
        A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12:
            return None
        for q in range(k):
            if q != c:
                f = A[q][c] / A[c][c]
                for j in range(c, k + 1):
                    A[q][j] -= f * A[c][j]
    b = [A[i][k] / A[i][i] for i in range(k)]
    my = sum(Y) / n
    ss = sum((y - my) ** 2 for y in Y)
    rs = sum((Y[i] - sum(b[a] * X[i][a] for a in range(k))) ** 2
             for i in range(n))
    return b, (1 - rs / ss if ss else 0.0)


def daily_from_retro(store):
    """Daily mean temperature and total demand, solar added back."""
    T, D = store["temp_C"], store["demand_GW"]
    S = store.get("solar_GW") or []
    t0 = dt.datetime.fromisoformat(store["start_day"])
    buckets = {}
    for i in range(min(len(T), len(D))):
        if T[i] is None or D[i] is None:
            continue
        ts = t0 + dt.timedelta(hours=i)
        # demand_GW is ALREADY underlying - see the note in retro.py. Adding
        # embedded solar here double-counted it and inflated every daytime
        # slope. Peer review, 17 Aug 2026.
        s = (S[i] or 0.0) if i < len(S) else 0.0
        buckets.setdefault(ts.date(), []).append((T[i], D[i], s))
    rows = []
    for d_, v in sorted(buckets.items()):
        if len(v) < 20:
            continue
        rows.append({
            "d": d_,
            "t": sum(x[0] for x in v) / len(v),
            "y": sum(x[1] for x in v) / len(v) * 24.0,   # GWh/day
            "solar": sum(x[2] for x in v),               # GWh/day
        })
    return rows


def attach_calendar(rows):
    for x in rows:
        d_ = x["d"]
        x["wknd"] = 1.0 if d_.weekday() >= 5 else 0.0
        x["hol"] = 1.0 if (d_.year, d_.month, d_.day) in BANK_HOLIDAYS else 0.0
        x["xmas"] = 1.0 if ((d_.month == 12 and d_.day >= 22)
                            or (d_.month == 1 and d_.day <= 2)) else 0.0
        x["aug"] = 1.0 if d_.month == 8 else 0.0
    t0 = rows[0]["d"]
    for x in rows:
        x["trend"] = (x["d"] - t0).days / 365.0


def attach_humidity(rows, hum):
    """Join dewpoint and enthalpy days by date. Returns how many matched."""
    by = {d_: (dw, ed) for d_, dw, ed in
          zip(hum["dates"], hum["dewpoint_mean_C"], hum["enthalpy_days"])}
    hit = 0
    for x in rows:
        v = by.get(x["d"].isoformat())
        if v:
            x["dew"], x["enth"] = v
            hit += 1
        else:
            x["dew"] = x["enth"] = None
    return hit


def fit_at(rows, Tc, extras):
    """Base model plus named extra regressors. Returns (coefs, R2, names)."""
    names = ["const", "weekend", "HDD%.1f" % HDD_BASE, "cool(T-%.1f)^2" % Tc]
    Y, X = [], []
    for x in rows:
        if any(x.get(k) is None for _, k in extras):
            continue
        row = [1.0, x["wknd"], max(0.0, HDD_BASE - x["t"]),
               max(0.0, x["t"] - Tc) ** 2]
        row += [float(x[k]) for _, k in extras]
        X.append(row)
        Y.append(x["y"])
    if len(Y) < 60:
        return None
    res = ols(Y, X)
    if not res:
        return None
    b, r2 = res
    return b, r2, names + [lab for lab, _ in extras], len(Y)


def cooling_twh(rows, Tc, coef, used):
    """Annualised cooling electricity implied by the fitted limb."""
    tot = sum(coef * max(0.0, x["t"] - Tc) ** 2 for x in rows)
    return tot * 365.0 / used / 1000.0


def scan_tc(rows, extras):
    best = None
    for Tc in TC_SCAN:
        r = fit_at(rows, Tc, extras)
        if not r:
            continue
        b, r2, names, n = r
        if b[3] <= 0:
            continue
        if best is None or r2 > best[1]:
            best = (Tc, r2, b, names, n)
    return best


def main(retro_path, hum_path=None):
    store = json.load(open(retro_path))
    rows = daily_from_retro(store)
    attach_calendar(rows)
    have_hum = False
    if hum_path:
        hit = attach_humidity(rows, json.load(open(hum_path)))
        have_hum = hit >= 60
        print("humidity joined on %d of %d days%s\n"
              % (hit, len(rows), "" if have_hum else "  - TOO FEW, skipping"))
    else:
        for x in rows:
            x["dew"] = x["enth"] = None
        print("no humidity file given - dewpoint and enthalpy terms skipped\n")

    print("%d days, %s to %s" % (len(rows), rows[0]["d"], rows[-1]["d"]))
    print("ECUK cooling anchor %.2f TWh/yr (%.0f%% of %.1f)\n"
          % (ANCHOR_TWH, 100 * COOL_SHARE, ECUK_COOL_VENT_TWH))

    base = scan_tc(rows, [])
    if not base:
        print("base model failed to fit")
        return
    Tc0, r20, b0, _, n0 = base
    cool0 = cooling_twh(rows, Tc0, b0[3], n0)
    print("BASE  const + weekend + HDD + convex cooling limb")
    print("  balance point %.1f degC   R2 %.4f   cooling %.2f TWh/yr "
          "(%.0f%% of anchor)\n" % (Tc0, r20, cool0, 100 * cool0 / ANCHOR_TWH))

    tests = [
        ("solar (embedded PV)", [("solar", "solar")]),
        ("bank holidays", [("hol", "hol")]),
        ("Christmas shutdown", [("xmas", "xmas")]),
        ("August activity dip", [("aug", "aug")]),
        ("linear trend", [("trend", "trend")]),
    ]
    if have_hum:
        tests += [("dewpoint (absolute moisture)", [("dew", "dew")]),
                  ("enthalpy days (sensible+latent)", [("enth", "enth")])]

    print("EACH TERM SEPARATELY, against that same base\n")
    print("  %-34s %8s %9s %9s %11s" %
          ("term", "R2", "dR2", "Tc", "cooling TWh"))
    for lab, extra in tests:
        r = scan_tc(rows, extra)
        if not r:
            print("  %-34s  did not fit" % lab)
            continue
        Tc, r2, b, names, n = r
        c = cooling_twh(rows, Tc, b[3], n)
        print("  %-34s %8.4f %+9.4f %9.1f %11.2f   coef %+.4f"
              % (lab, r2, r2 - r20, Tc, c, b[4]))

    allx = [e for _, ex in tests for e in ex]
    r = scan_tc(rows, allx)
    if r:
        Tc, r2, b, names, n = r
        c = cooling_twh(rows, Tc, b[3], n)
        print("\n  %-34s %8.4f %+9.4f %9.1f %11.2f"
              % ("ALL TOGETHER", r2, r2 - r20, Tc, c))
        print("\n  coefficients at the best balance point:")
        for nm, v in zip(names, b):
            print("     %-22s %+12.4f" % (nm, v))
        print("\n  cooling limb is %.0f%% of the ECUK anchor"
              % (100 * c / ANCHOR_TWH))

    if have_hum:
        print("\nTHE TEST THAT MATTERS")
        print("  If the convexity is LATENT LOAD, adding enthalpy days should")
        print("  absorb part of the quadratic term and its coefficient should")
        print("  fall materially. If the quadratic barely moves, the bend is")
        print("  the EXTENSIVE MARGIN - more buildings switching on - and the")
        print("  extensive-margin reading stands.")
        r = scan_tc(rows, [("enth", "enth")])
        if r:
            print("  quadratic coef: %.4f base -> %.4f with enthalpy  (%+.0f%%)"
                  % (b0[3], r[2][3], 100 * (r[2][3] / b0[3] - 1)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
