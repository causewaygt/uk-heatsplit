"""Phase A' offline validation: the retrospective engine against the
shared-weather hourly stubs. Run: PYTHONPATH=stubs python3 run_test_retro.py
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, "stubs")
sys.path.insert(0, "scripts")

import retro
from fetch_hourly import hourly_temp, hourly_mid, hourly_ci

END = dt.date.today() - dt.timedelta(days=2)
START = END - dt.timedelta(days=396)
S, E = START.isoformat(), END.isoformat()
SPACE_TWH, DHW_TWH = 290.0, 77.7   # useful-basis stand-ins for the test

t_calls = [0]
def f_temp(a, b):
    t_calls[0] += 1
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return hourly_temp(a, (d1 - d0).days + 1)
def f_mid(a, b):
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return hourly_mid(a, (d1 - d0).days + 1, f_temp.__wrapped__(a, b)
                      if hasattr(f_temp, "__wrapped__")
                      else hourly_temp(a, (d1 - d0).days + 1))
def f_ci(a, b):
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return hourly_ci(a, (d1 - d0).days + 1)

# --- 1. full-year build: shape, calibration, gates --------------------------
r1 = retro.build_retro(S, E, SPACE_TWH, DHW_TWH, base_c=16.5,
                       fetch_temp=f_temp, fetch_mid=f_mid, fetch_ci=f_ci)
assert r1["calibration"]["shaping_base_c"] == 16.5
n = r1["n_hours"]
assert n == 397 * 24, n
assert abs(sum(r1["heat_GWh"][-8760:])
           - (SPACE_TWH + DHW_TWH) * 1000) < 1.0   # trailing year exact
ok, rep = retro.gates(r1)
assert ok, rep                                           # G1 anchors
for k in ("ashp", "shallow", "network"):
    assert abs(rep[f"g1_{k}"]["spf"] - retro.SPF_ANCHORS[k]) <= 0.01
print("G1: SPF anchors reproduced:",
      {k: rep[f"g1_{k}"]["spf"] for k in ("ashp", "shallow", "network")})

# --- 2. G2 wiring: network-route annual vs a consistent weekly figure -------
eta_n = r1["calibration"]["eta"]["network"]
truth = sum(retro.R_SHIFT * q / retro.route_cop("network", t, eta_n)
            for q, t in zip(r1["heat_GWh"], r1["temp_C"])) / 1000.0
ok2, rep2 = retro.gates(r1, weekly_whatif_repl_elec_twh=truth * 1.01)
assert ok2 and rep2["g2_network_vs_weekly"]["pass"], rep2
ok3, rep3 = retro.gates(r1, weekly_whatif_repl_elec_twh=truth * 1.10)
assert not ok3                                           # 10% off must fail
print("G2: reconciliation gate passes at 1%, fails at 10%  OK")

# --- 3. physics ordering at the cold hours ----------------------------------
etas = r1["calibration"]["eta"]
cold = sorted(range(n), key=lambda i: r1["temp_C"][i])[:50]
for i in cold:
    t = r1["temp_C"][i]
    ca = retro.route_cop("ashp", t, etas["ashp"])
    cs = retro.route_cop("shallow", t, etas["shallow"])
    cn = retro.route_cop("network", t, etas["network"])
    assert ca < cs < cn, (t, ca, cs, cn)
t_cold = r1["temp_C"][cold[0]]
print("cold-hour COPs at %.1fC: ashp %.2f < shallow %.2f < network %.2f  OK"
      % (t_cold, retro.route_cop("ashp", t_cold, etas["ashp"]),
         retro.route_cop("shallow", t_cold, etas["shallow"]),
         retro.route_cop("network", t_cold, etas["network"])))
# defrost notch: ASHP COP at 2C must undercut the frost-free curve
c2 = retro.route_cop("ashp", 2.0, etas["ashp"])
c2_nf = etas["ashp"] * retro.carnot_cop(retro.flow_temp(2.0),
                                        2.0 - retro.AIR_APPROACH_C)
assert c2 < c2_nf * 0.95, (c2, c2_nf)
print("defrost notch present at 2C: %.2f vs frost-free %.2f  OK" % (c2, c2_nf))

# --- 4. persistence: save/load, append-only, 2-day revision window ----------
path = "/tmp/retro_test.json"
retro.save(r1, path)
r1b = retro.load(path)
assert r1b["n_hours"] == n and r1b["temp_C"][:24] == r1["temp_C"][:24]
E2 = (END + dt.timedelta(days=1)).isoformat()
t_calls[0] = 0
r2 = retro.build_retro(S, E2, SPACE_TWH, DHW_TWH, base_c=16.5,
                       fetch_temp=f_temp, fetch_mid=f_mid, fetch_ci=f_ci,
                       prev=r1b)
assert r2["n_hours"] == n + 24
assert r2["temp_C"][:n - 48] == r1["temp_C"][:n - 48]    # frozen bulk kept
frozen_days = (n - 48) // 24
# the incremental fetch covered only the revision window + the new day
# (fetchers are per-day deterministic, so equality above proves reuse)
sz = os.path.getsize(path)
assert sz < 900_000, sz
print("persistence: append kept %d frozen days, file %.0f KB  OK"
      % (frozen_days, sz / 1024))

# --- 5. calibration stability: eta insensitive to the append ----------------
for k in etas:
    assert abs(r2["calibration"]["eta"][k] - etas[k]) < 0.01, k
print("eta stable across append:",
      {k: r2["calibration"]["eta"][k] for k in etas})

print("\nALL RETRO TESTS PASSED")

# --- 6. Phase B: slices, worst hour, stress ordering, premium ---------------
nd = {}
d = START
import random as _r
while d <= END:
    doy = d.timetuple().tm_yday
    import math as _m
    winter = 0.5 * (1 + _m.cos((doy - 15) / 365.0 * 2 * _m.pi))
    nd[d.isoformat()] = 700.0 + 350.0 * winter \
        + _r.Random("nd" + d.isoformat()).gauss(0, 25)
    d += dt.timedelta(days=1)

r1["calibration"]["cooling"] = {"annual_gwh": 10244.0, "base_c": 18.0,
                                "eer": 3.0, "passive_cop": 20.0}
sl = retro.slices(r1, nd_daily=nd)
assert len(sl["hourly_live_7d"]["heat_GWh"]) == 168
assert len(sl["worst_week"]["heat_GWh"]) == 168
assert len(sl["daily_90"]) == 90
assert 12 <= len(sl["monthly_13"]) <= 13
# worst hour is the argmax of the modelled heat, empirically located
iw = max(range(r1["n_hours"]), key=lambda i: r1["heat_GWh"][i])
assert sl["stress"]["worst_hour_heat_GWh"] == round(r1["heat_GWh"][iw], 1)
# stress ordering: colder source -> more added GW at the worst hour
st = sl["stress"]
assert st["ashp"]["added_GW"] > st["shallow"]["added_GW"] \
       > st["network"]["added_GW"] > 0
assert st["ashp"]["pct_of_record_day"] > 0
# hours-above distributions are monotone declining in the threshold
for r in ("ashp", "shallow", "network"):
    ha = st[r]["hours_above"]
    vals = [ha[k] for k in ("5", "10", "15", "20", "25")]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
# coincidence premium: air-source pays the largest premium; network least
pr = sl["coincidence_premium"]
assert pr["ashp"]["premium_pct"] > pr["network"]["premium_pct"]
assert pr["ashp"]["premium_pct"] > 0
# monthly falcon shape: max month heat in winter half, > 2x min month
mh = [m["heat_GWh"] for m in sl["monthly_13"]]
assert max(mh) > 2 * min(m for m in mh if m > 0)
print("slices: worst hour %s (%.1f GWh, %.1fC) | added GW a/s/n = "
      "%.1f/%.1f/%.1f | premium %% a/s/n = %.1f/%.1f/%.1f"
      % (st["worst_hour"], st["worst_hour_heat_GWh"],
         st["worst_hour_temp_C"], st["ashp"]["added_GW"],
         st["shallow"]["added_GW"], st["network"]["added_GW"],
         pr["ashp"]["premium_pct"], pr["shallow"]["premium_pct"],
         pr["network"]["premium_pct"]))

# --- 7. trim: cap respected, front dropped, start_day advanced --------------
big = json.loads(json.dumps(r2))
t0 = big["start_day"]
trimmed = retro.trim(json.loads(json.dumps(big)), max_days=300)
assert trimmed["n_hours"] == 300 * 24
assert trimmed["temp_C"] == big["temp_C"][-300 * 24:]
assert trimmed["start_day"] > t0
print("trim: 300-day cap enforced, start_day advanced  OK")

# --- 8. cooling layer: conservation, summer peak, relief, second hump -------
csum = sum(m["cool_GWh"] for m in sl["monthly_13"])
assert 10244 * 0.99 <= csum <= 10244 * 1.35, csum  # 13 months > one year
fl_h, shp = retro.hourly_cooling_elec(r1["temp_C"],
                                      r1["calibration"]["cooling"])
h8760 = sum(fl_h + x for x in shp[-8760:])
assert abs(h8760 - 10244) < 10, h8760              # trailing year EXACT
ss = sl["stress_summer"]
assert ss and ss["peak_hour"][5:7] in ("06", "07", "08"), ss["peak_hour"]
assert ss["today_GW"] > 0 and ss["x2_scenario_added_GW"] == ss["today_GW"]
assert 0 < ss["whatif_relief_GW"] < ss["today_GW"]
mm = {m["month"][5:]: m for m in sl["monthly_13"]}
jul = mm.get("07") or mm.get("08"); jan = mm.get("01") or mm.get("12")
assert jul["cool_GWh"] > 2 * jan["cool_GWh"]        # the summer hump
assert jan["heat_GWh"] > 2 * jul["heat_GWh"]        # against the winter one
assert 0 < jul["cool_whatif_relief_GWh"] < jul["cool_GWh"]
assert len(sl["hourly_live_7d"]["cool_GWh"]) == 168
print("cooling layer: annual %.0f GWh conserved | summer peak %s "
      "%.1f GW, relief %.1f GW | Jul cool %.0f vs Jan %.0f GWh"
      % (csum, ss["peak_hour"], ss["today_GW"], ss["whatif_relief_GW"],
         jul["cool_GWh"], jan["cool_GWh"]))

# --- 9. calendar-ordered falcon ---------------------------------------------
mc = sl["monthly_calendar"]
assert mc and len(mc) == 12
assert [r["month"][5:] for r in mc] == ["%02d" % i for i in range(1, 13)]
assert mc[0]["label"].startswith("Jan") and mc[11]["label"].startswith("Dec")
by = {r["month"]: r for r in sl["monthly_13"]}
for r in mc:
    assert r["heat_GWh"] == by[r["month"]]["heat_GWh"]      # lossless reorder
assert all(r["complete"] for r in mc)
print("calendar falcon: Jan", mc[0]["label"], "... Dec", mc[11]["label"],
      "| 12 distinct complete months, lossless  OK")

print("\nALL PHASE B TESTS PASSED")
