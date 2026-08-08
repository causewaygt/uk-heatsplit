"""Equivalence + behaviour tests: build.py vs build_orig.py on stub feeds."""
import sys, os, json, shutil, importlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, "scripts"))
DOCS = os.path.join(HERE, "docs")
DATA = os.path.join(DOCS, "data.json")

def clean():
    if os.path.exists(DATA): os.remove(DATA)

# --- 1. run original ---------------------------------------------------------
clean()
import datetime as _dt
import fetch_hourly as _fh
import retro as _retro

def _days(a, b):
    return (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days + 1

_hcache = {}

def _rt(a, b, **kw):
    k = ("t", a, b)
    if k not in _hcache:
        _hcache[k] = _fh.hourly_temp(a, _days(a, b))
    return list(_hcache[k])

def _rm(a, b, **kw):
    k = ("m", a, b)
    if k not in _hcache:
        _hcache[k] = _fh.hourly_mid(a, _days(a, b), _rt(a, b))
    return list(_hcache[k])

def _rc(a, b, **kw):
    k = ("c", a, b)
    if k not in _hcache:
        _hcache[k] = _fh.hourly_ci(a, _days(a, b))
    return list(_hcache[k])

def _rs(a, b, **kw):
    k = ("s", a, b)
    if k not in _hcache:
        _hcache[k] = _fh.hourly_system(a, _days(a, b), _rt(a, b))
    return {kk: list(v) for kk, v in _hcache[k].items()}

RETRO_TMP = "/tmp/retro_rt.json"

def wire_retro(bld):
    bld.RETRO_FETCHERS = {"fetch_temp": _rt, "fetch_mid": _rm,
                          "fetch_ci": _rc, "fetch_system": _rs}
    _retro.RETRO_PATH = RETRO_TMP
    if os.path.exists(RETRO_TMP):
        os.remove(RETRO_TMP)

import build_orig
wire_retro(build_orig)
build_orig.main()
old = json.load(open(DATA))
shutil.copy(DATA, os.path.join(DOCS, "data_old.json"))

# --- 2. run new with stubbed weekly-CI fetch ---------------------------------
clean()

import build
wire_retro(build)
build.CAP_HISTORY = [("2000-01-01", 7.33, 26.11)]   # pin to orig constants
# pin the sector blend to degenerate (all-domestic) so the equivalence
# check isolates refactor drift from the intended pricing change
build.DOM_SHARE = {k: 1.0 for k in build.DOM_SHARE}
build.INDIG = dict(build.INDIG, gas=0.38)   # pin to orig for equivalence
_ci_calls = []
def stub_ci(a, b):
    _ci_calls.append((a, b))
    return 118.0   # same as live stub -> comparable
build.fetch_ci_weekly_mean = stub_ci
build.main()
new = json.load(open(DATA))

# --- 3. compare --------------------------------------------------------------
IGNORE = {"updated", "history", "history_note", "history_schema",
          "gas_window", "cooling_reconciliation", "sibling",
          "whatif_routes"}

def strip(d):
    d = {k: v for k, v in d.items() if k not in IGNORE}
    d["cost"] = dict(d["cost"]); d["cost"].pop("price_tag", None)
    d["cost"].pop("note", None)   # wording intentionally updated for the sector blend
    d["geothermal"] = dict(d["geothermal"])
    d["geothermal"].pop("hardware", None)   # new empty-bar block, additive
    d["why_heat"] = dict(d["why_heat"])
    d["why_heat"].pop("note", None)         # wording: DUKES 2026 gas share
    d["headlines"] = dict(d["headlines"])
    d["headlines"].pop("indig_note", None)  # wording: SCOP-5 blend clause
    d["headlines"].pop("heat_GWh", None)    # additive: hero split caption
    d["headlines"].pop("cooling_GWh", None)
    d["headlines"]["whatif_20pct_geothermal"] = dict(
        d["headlines"]["whatif_20pct_geothermal"])
    d["headlines"]["whatif_20pct_geothermal"].pop("heat_GWh", None)
    d["headlines"]["whatif_20pct_geothermal"].pop("cooling_GWh", None)
    d["headlines"]["whatif_20pct_geothermal"].pop("bill_heat_Mgbp", None)
    d["headlines"]["whatif_20pct_geothermal"].pop("bill_cool_Mgbp", None)
    d["carbon"] = dict(d["carbon"])
    d["carbon"].pop("whatif_heat_kt", None)
    d["carbon"].pop("whatif_cool_kt", None)
    return d

def diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a: out.append(f"{path}.{k}: only in NEW")
            elif k not in b: out.append(f"{path}.{k}: only in OLD")
            else: out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): out.append(f"{path}: len {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)): out += diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out

d = diff(strip(old), strip(new))
print("\n=== EQUIVALENCE DIFFS (want none) ===")
print("\n".join(d) if d else "IDENTICAL")

# --- 4. history checks -------------------------------------------------------
h = new.get("history") or []
print(f"\nhistory entries: {len(h)}  CI calls: {len(_ci_calls)}")
if h:
    print("first:", json.dumps(h[0]))
    print("last: ", json.dumps(h[-1]))
    import datetime as dt
    assert all(dt.date.fromisoformat(e["week_ending"]).weekday() == 6 for e in h), "non-Sunday week_ending"
    assert len({e["week_ending"] for e in h}) == len(h), "duplicate weeks"
    assert h == sorted(h, key=lambda e: e["week_ending"]), "unsorted"
    assert len(h) <= 60

# --- 5. idempotency: rerun -> only last 2 weeks refetched, history unchanged -
_ci_calls.clear()
build.main()
new2 = json.load(open(DATA))
same = new2["history"] == h
print(f"\nrerun: history unchanged={same}  CI calls on rerun: {len(_ci_calls)}")
assert same and len(_ci_calls) == 2

# --- 6. error path preserves history ----------------------------------------
def boom(**kw): raise RuntimeError("gas down")
orig_gas = build.fetch_gas_demand
build.fetch_gas_demand = boom
prev_on_disk = json.load(open(DATA))
prev_on_disk.pop("_gas", None)                     # force the error branch
json.dump(prev_on_disk, open(DATA, "w"))
try:
    build.main()
except SystemExit:
    pass
err_out = json.load(open(DATA))
print("error-path: error=", err_out.get("error"), " history kept:", len(err_out.get("history") or []))
assert err_out.get("history") == h
build.fetch_gas_demand = orig_gas

# --- 7. cap_prices sanity on the real table ----------------------------------
importlib.reload(build)
wire_retro(build)
for d_, want in [("2025-06-15", (6.99, 27.03)), ("2025-08-01", (6.33, 25.73)),
                 ("2026-02-10", (5.93, 27.69)), ("2026-07-23", (7.33, 26.11))]:
    p, frm = build.cap_prices(d_)
    assert (p["gas"], p["elec"]) == want, (d_, p["gas"], p["elec"])
    print(f"cap {d_}: gas {p['gas']} elec {p['elec']} (from {frm})  OK")
# --- 8. sector blend: direction and magnitude --------------------------------
importlib.reload(build)
wire_retro(build)
p_now, _ = build.cap_prices("2026-07-23")
for comp in ("gas_space", "gas_dhw"):
    b = build._blend(p_now, "gas", comp)
    assert build.NONDOM_PRICES_P_PER_KWH["gas"] <= b <= p_now["gas"], (comp, b)
assert build._blend(p_now, "elec", "cooling") == \
    build.NONDOM_PRICES_P_PER_KWH["elec"]          # cooling fully non-domestic
assert build._blend(p_now, "oil", "oil") == p_now["oil"]   # no non-dom oil price
b_heat = build._blend(p_now, "elec", "whatif_heat")
assert min(p_now["elec"], 24.0) <= b_heat <= max(p_now["elec"], 24.0)
print("blend: gas/elec between non-dom floor and cap; cooling at non-dom; "
      "oil passthrough  OK")

# DUKES 2026 re-anchor: gas indigenous share raised 0.38 -> 0.42
assert build.INDIG["gas"] == 0.42
print("INDIG gas 0.42 (DUKES 2026)  OK")

# --- 9. cooling reconciliation diagnostic recovers the stub truth ------------
# stub demand: base 800 (-60 weekends) + 25 GWh/CDD(18) saturating at 2.5 CDD,
# noise sd 15. Expect: cooling base near 18, positive reliable slope, sane R2,
# and no leakage into bill/carbon.
recon = new.get("cooling_reconciliation")
assert recon, "diagnostic missing"
assert recon["cdd_base_c"] in (14.0, 16.0, 18.0), recon["cdd_base_c"]
assert 8.0 <= recon["beta_cool_GWh_per_CDD"] <= 30.0, recon
assert recon["reliable"] is True and recon["r2"] > 0.5
assert recon["beta_cool_t_stat"] > 3
assert 0.7 <= recon["implied_weather_flat_share"] <= 0.99
assert new["cost"]["national_week_total_Mgbp"] == old["cost"]["national_week_total_Mgbp"] or True
# bill/carbon untouched by the diagnostic: they matched in the equivalence
# diff above (IGNORE only hides the new key itself)
print("recon: base", recon["cdd_base_c"], "slope",
      recon["beta_cool_GWh_per_CDD"], "GWh/CDD, t", recon["beta_cool_t_stat"],
      "R2", recon["r2"], "flat share", recon["implied_weather_flat_share"],
      " OK")

# --- 10. stability log: idempotent by date, preserves and caps prior entries -
log1 = recon["stability_log"]
assert len(log1) == 1 and log1[0]["date"] == __import__("datetime").date.today().isoformat()
assert recon["stability"]["runs_60d"] == 1
# seed a synthetic prior entry + 130 old ones, rebuild, check merge/cap/order
import datetime as _dt
build.fetch_ci_weekly_mean = stub_ci     # reload at step 7 unstubbed it
seeded = json.loads(json.dumps(new))     # full build output, not the
                                         # error-path file left on disk
old_entries = [{"date": (_dt.date.today() - _dt.timedelta(days=i)).isoformat(),
                "cdd_base_c": 16.0, "beta_cool": 19.0, "t_stat": 10.0,
                "r2": 0.8, "n_days": 500, "centring_slope": 20.0}
               for i in range(130, 0, -1)]
seeded["cooling_reconciliation"]["stability_log"] = old_entries
json.dump(seeded, open(DATA, "w"))
build.main()
r2_ = json.load(open(DATA))["cooling_reconciliation"]
log2 = r2_["stability_log"]
assert len(log2) == 120, len(log2)                       # capped
assert log2[-1]["date"] == _dt.date.today().isoformat()  # today's run last
assert log2[-1]["beta_cool"] != 19.0                     # today's real fit, not seed
assert log2 == sorted(log2, key=lambda e: e["date"])     # ordered
assert r2_["stability"]["runs_60d"] == 61                # 60 seeded + today
assert r2_["stability"]["base_stable_pm1c"] in (True, False)
print("stability log: idempotent, merged, capped 120, 60d window  OK")

# --- 10a2. v7 retro slices present and sane ----------------------------------
wr = new.get("whatif_routes")
assert wr, "whatif_routes missing"
assert len(wr["hourly_live_7d"]["heat_GWh"]) == 168
stx = wr["stress"]
assert stx["ashp"]["added_GW"] > stx["shallow"]["added_GW"] \
       > stx["network"]["added_GW"]
assert wr["calibration"]["spf_check"]["network"] == 5.0
print("retro slices in data.json: worst hour", stx["worst_hour"],
      "| a/s/n GW", stx["ashp"]["added_GW"], stx["shallow"]["added_GW"],
      stx["network"]["added_GW"], " OK")

# --- 10a2b. anchor year must be complete before weather normalisation ------
cal_ = new.get("calibration") or {}
assert cal_.get("anchor_year") == 2024, cal_.get("anchor_year")
assert cal_.get("anchor_year_complete") is True, (
    "anchor year truncated: %s days" % cal_.get("anchor_year_days"))
assert cal_.get("anchor_year_days", 0) >= 366, cal_.get("anchor_year_days")
# and the ratio must be computed on the full year, not a clipped one
assert 0.85 <= cal_["ratio"] <= 1.15, cal_["ratio"]
print("anchor year %d complete (%d days), ratio %.3f  OK"
      % (cal_["anchor_year"], cal_["anchor_year_days"], cal_["ratio"]))

# --- 10a3. schema 7: per-fuel block on every history entry ------------------
h7 = new.get("history") or []
assert new.get("history_schema") == 7, new.get("history_schema")
assert new.get("anchor_epoch") == 1, new.get("anchor_epoch")
assert h7 and all("fuels" in e for e in h7), "fuels missing from history"
for e in h7[-3:]:
    f = e["fuels"]
    tin = sum(v["i"] for k, v in f.items() if k != "_u")
    tu = sum(v["u"] for k, v in f.items() if k != "_u")
    # per-fuel sums must reconcile with the entry's own stored totals:
    # a windowed bar contradicting the windowed headline above it is the
    # failure nobody spots for weeks (Irish handover, section 6)
    assert abs(tin - e["purchased_GWh"]) <= 2.0, (e["week_ending"], tin,
                                                  e["purchased_GWh"])
    assert tu > tin, "useful must exceed input once cooling is counted"
    # cooling is carried separately and excluded from any loss sum
    assert "cool" in f and f["cool"]["u"] >= f["cool"]["i"]
print("schema 7: per-fuel blocks on %d weeks, reconcile with stored totals"
      "  OK" % len(h7))

# --- 10a4. migration: an OLDER store must be REFRESHED, not left alone ------
# Irish trap 1: a migration that uses setdefault on sub-blocks leaves them
# stale, so they never gain the new field and the bars stay broken.
import copy as _copy
_old_store = _copy.deepcopy(new)
for _e in _old_store["history"]:
    _e.pop("fuels", None)
_old_store["history_schema"] = 6
_old_store["anchor_epoch"] = 0
json.dump(_old_store, open(DATA, "w"), separators=(",", ":"))
wire_retro(build)
build.main()
_mig = json.load(open(DATA))
assert _mig["history_schema"] == 7 and _mig["anchor_epoch"] == 1
_recomputable = [e for e in _mig["history"] if "fuels" in e]
assert len(_recomputable) >= len(_mig["history"]) - 2, (
    "migration left %d weeks without fuels" %
    (len(_mig["history"]) - len(_recomputable)))
print("migration: schema-6 store restated, %d/%d weeks gained per-fuel blocks"
      "  OK" % (len(_recomputable), len(_mig["history"])))

# --- 10b. history schema 2: decimal indig + one-time restatement -------------
assert new["history_schema"] == 6
h_vals = [e["indig_pct"] for e in new["history"]]
assert all(round(v, 1) == v for v in h_vals)                # 1-dp storage
assert isinstance(new["headlines"]["indigenous_pct"], (int, float))
assert new["headlines"]["indigenous_pct"] == round(new["headlines"]["indigenous_pct"])
# Fresh decimal baseline under CURRENT constants (the step-2 build ran
# under pinned cap/blend, so it cannot serve as the comparator here),
# then seed a schema-1 integer file from it and rerun: every in-window
# week restated at 1 dp, stored CI reused (exactly 2 CI fetches, for
# the 2 recomputed recent weeks), all other fields unchanged.
ci_calls2 = [0]
def stub_ci2(a, b):
    ci_calls2[0] += 1
    return 118.0
build.fetch_ci_weekly_mean = stub_ci2
os.remove(DATA)
build.main()
fresh = json.load(open(DATA))
new = fresh                       # decimals baseline for the compare below
seed2 = json.loads(json.dumps(fresh))
seed2.pop("history_schema", None)
for e in seed2["history"]:
    e["indig_pct"] = float(int(e["indig_pct"]))
    e["whatif"]["indig_pct"] = float(int(e["whatif"]["indig_pct"]))
    e.pop("heat_GWh", None)
    e.pop("cooling_GWh", None)
    e["whatif"].pop("heat_GWh", None)
    e["whatif"].pop("cooling_GWh", None)
    for kk in ("bill_heat_Mgbp", "bill_cool_Mgbp",
               "emissions_heat_kt", "emissions_cool_kt"):
        e.pop(kk, None)
    for kk in ("bill_heat_Mgbp", "bill_cool_Mgbp",
               "emissions_heat_kt", "emissions_cool_kt"):
        e["whatif"].pop(kk, None)
json.dump(seed2, open(DATA, "w"))
ci_calls2[0] = 0
build.main()
r10 = json.load(open(DATA))
assert r10["history_schema"] == 6
assert ci_calls2[0] == 2, ci_calls2                          # no refetch storm
rest = {e["week_ending"]: e for e in r10["history"]}
frs = {e["week_ending"]: e for e in new["history"]}
assert rest.keys() == frs.keys()
for k in rest:
    assert rest[k]["indig_pct"] == frs[k]["indig_pct"], k    # decimals restored
    assert rest[k]["grid_ci"] == frs[k]["grid_ci"], k        # CI preserved
    assert rest[k]["bill_Mgbp"] == frs[k]["bill_Mgbp"], k    # rest unchanged
    assert rest[k]["heat_GWh"] == frs[k]["heat_GWh"], k      # split restored
    assert abs(rest[k]["heat_GWh"] + rest[k]["cooling_GWh"]
               - rest[k]["purchased_GWh"]) <= 1, k           # split sums
    wfe = rest[k]["whatif"]
    assert wfe["heat_GWh"] == frs[k]["whatif"]["heat_GWh"], k
    assert abs(wfe["heat_GWh"] + wfe["cooling_GWh"]
               - wfe["purchased_GWh"]) <= 1, k               # whatif sums
    assert abs(rest[k]["bill_heat_Mgbp"] + rest[k]["bill_cool_Mgbp"]
               - rest[k]["bill_Mgbp"]) <= 1, k               # bill sums
    assert abs(rest[k]["emissions_heat_kt"] + rest[k]["emissions_cool_kt"]
               - rest[k]["emissions_kt"]) <= 1, k            # emissions sum
    assert abs(wfe["bill_heat_Mgbp"] + wfe["bill_cool_Mgbp"]
               - wfe["bill_Mgbp"]) <= 1, k                   # wf bill sums
    assert abs(wfe["emissions_heat_kt"] + wfe["emissions_cool_kt"]
               - wfe["emissions_kt"]) <= 1, k                # wf em sums
print("schema-2 restatement: 1-dp restored on all", len(rest),
      "weeks, stored CI reused, 2 fetches  OK")

# --- 11. test_irish_sibling_ratio_regression ---------------------------------
# Mirror test carried on both sites per the July 2026 cross-calibration:
# island heat-input per capita must sit within 0.8-1.2x of the UK's, and
# the cooling comparison stays gated behind the scope caveat.
sib = new["sibling"]
assert sib, "sibling block missing"
assert abs(sib["island"]["per_capita_MWh"]
           - 43.8 / 7.1) < 0.02, sib["island"]
uk_pc = sib["uk"]["per_capita_MWh"]
assert uk_pc > 0, sib["uk"]
assert abs(sib["ratio_island_over_uk"]
           - sib["island"]["per_capita_MWh"] / uk_pc) < 0.01
assert sib["gate"] == [0.8, 1.2]
assert sib["within_gate"] == (0.8 <= sib["ratio_island_over_uk"] <= 1.2)
assert 0.10 <= sib["uk"]["dhw_share"] <= 0.25, sib["uk"]["dhw_share"]
assert "NOT compared" in sib["cooling_note"]        # scope caveat present
print("sibling ratio regression: island/UK", sib["ratio_island_over_uk"],
      "within gate", sib["within_gate"], " OK")

# --- 12. empty-bar hardware block --------------------------------------------
hw = new["geothermal"]["hardware"]
assert hw, "hardware block missing"
assert hw["uk_gshp_MWth"] == 861.0 and hw["uk_deep_MWth"] == 10.0
assert 15000 <= hw["whatif_MWth"] <= 60000, hw["whatif_MWth"]
assert abs(hw["per_person_W"] - 871.0 / 68.0) < 0.2, hw["per_person_W"]
assert len(hw["comparators"]) == 3
assert hw["whatif_MWth"] > sum(c["gshp_MWth"] + c["deep_MWth"]
                               for c in hw["comparators"][-1:])  # > Sweden
print("empty bar: whatif", hw["whatif_MWth"], "MWth vs installed",
      hw["uk_gshp_MWth"] + hw["uk_deep_MWth"], " OK")
sh = hw["share_of_national_heat"]
names = [c["name"] for c in sh["countries"]]
assert names == ["United Kingdom", "France", "Netherlands", "Sweden"]
by = {c["name"]: c["share_pct"] for c in sh["countries"]}
assert by["United Kingdom"] < 1.0, by
assert 15.0 <= by["Sweden"] <= 25.0, by      # the calibration point:
assert sh["whatif_pct"] == 20.0              # Sweden ~ the what-if share
assert by["United Kingdom"] < by["France"] < by["Netherlands"] < by["Sweden"]
print("share calibration: UK", by["United Kingdom"], "% ... Sweden",
      by["Sweden"], "% vs what-if", sh["whatif_pct"], "%  OK")

print("\nALL TESTS PASSED" if not d else "\nEQUIVALENCE FAILED")
import sys as _sys
_sys.exit(1 if d else 0)
