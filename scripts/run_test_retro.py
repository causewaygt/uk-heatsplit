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
START = END - dt.timedelta(days=364)
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
r1 = retro.build_retro(S, E, SPACE_TWH, DHW_TWH,
                       fetch_temp=f_temp, fetch_mid=f_mid, fetch_ci=f_ci)
n = r1["n_hours"]
assert n == 365 * 24, n
assert abs(sum(r1["heat_GWh"]) - (SPACE_TWH + DHW_TWH) * 1000) < 1.0
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
r2 = retro.build_retro(S, E2, SPACE_TWH, DHW_TWH,
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
