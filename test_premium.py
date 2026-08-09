"""test_premium.py - the coincidence premium, winter basis.

NOTE: the premium panel was withdrawn from the site on 9 Aug 2026. The
statistic is still computed and logged every run, so these assertions
still guard it - if it is ever restored to the page it will be on the
winter basis, correct, and tested. Delete this file only if the
computation itself is removed from retro.py.

Runs retro.slices() on a synthetic year built so that the OLD trailing-year
premium goes negative while the within-winter coincidence is genuinely
positive - the exact situation that forced the basis change on 9 Aug 2026.
If the winter restriction is ever reverted, or the comparator silently goes
back to the annual mean price, these fail.

    python3 test_premium.py
"""
import datetime as dt
import math
import sys

import retro


def build_store(start="2025-07-01", days=400):
    """A year where summer prices are HIGH and winter prices track the
    cold, so heat - drawn almost entirely in winter - prices below the
    annual mean while correlating with price WITHIN winter. That is the
    coincidence the statistic is supposed to report, and the summer
    scarcity is the dilution that drowned it on the annual basis."""
    d0 = dt.date.fromisoformat(start)
    n = days * 24
    temps, mid, ci, heat = [], [], [], []
    for i in range(n):
        day = d0 + dt.timedelta(days=i // 24)
        hour = i % 24
        doy = day.timetuple().tm_yday
        # seasonal temperature, coldest in early January; diurnal minimum
        # before dawn, maximum mid-afternoon
        t = 10.5 - 8.0 * math.cos(2 * math.pi * (doy - 5) / 365.0)
        t -= 3.0 * math.cos(2 * math.pi * (hour - 15) / 24.0)
        temps.append(round(t, 2))
        summer = day.month in (5, 6, 7, 8, 9)
        if summer:
            p = 150.0            # scarcity: the annual-basis dilution
        else:
            p = 70.0 + 2.6 * max(0.0, 8.0 - t)   # cold hours are dear
        mid.append(round(p, 2))
        ci.append(120.0)
        hdh = max(0.0, 15.5 - t)
        heat.append(round(0.004 * hdh + 0.0009, 5))
    return {
        "schema": 2, "n_hours": n, "start_day": start,
        "basis": "synthetic fixture", "temp_C": temps, "mid_gbp_mwh": mid,
        "ci_g_kwh": ci, "heat_GWh": heat,
        "calibration": {"eta": {"ashp": 0.332, "shallow": 0.336,
                                "network": 0.487},
                        "dhw_annual_TWh": 64.7,
                        "spf_check": {"ashp": 2.8, "shallow": 3.24,
                                      "network": 5.0}},
    }


def main():
    store = build_store()
    try:
        sl = retro.slices(store)
    except Exception as exc:                       # pragma: no cover
        print("FAIL: slices() raised %r" % (exc,))
        return 1

    pr = sl["coincidence_premium_diagnostic"]
    fails = []

    def chk(name, cond, detail=""):
        if cond:
            print("  ok   " + name)
        else:
            fails.append(name)
            print("  FAIL " + name + (" - " + detail if detail else ""))

    print("coincidence premium: winter basis")

    chk("window is declared on the block",
        pr.get("window") == retro.WINTER_LABEL,
        "got %r" % (pr.get("window"),))

    # Oct-Mar of a trailing year: 182 days, allowing for the store's own
    # trailing-8760 cut falling mid-month.
    hrs = pr.get("hours", 0)
    chk("winter hour count is a heating season, not a year",
        3500 < hrs < 4500, "got %s hours" % hrs)

    chk("note states the window and the exclusion",
        retro.WINTER_LABEL in pr["note"]
        and "seasonal" in pr["note"],
        "note did not name the basis")

    for r in retro.ROUTES:
        chk("%s: winter premium is positive" % r,
            pr[r]["premium_pct"] > 0,
            "got %s" % pr[r]["premium_pct"])
        chk("%s: window recorded per route" % r,
            pr[r]["window"] == retro.WINTER_LABEL)

    # the comparator must be the SAME hours' mean, not the annual mean.
    # On this fixture the annual mean is dragged up by summer scarcity, so
    # pricing winter draw against it would give a large NEGATIVE number.
    chk("comparator uses the winter mean price, not the annual mean",
        60.0 < pr["ashp"]["mean_price_gbp_mwh"] < 100.0,
        "mean price %s" % pr["ashp"]["mean_price_gbp_mwh"])

    # and the annual figure - the one that broke - is carried, and is worse
    ad = pr.get("annual_diagnostic", {})
    chk("annual diagnostic carried for audit", set(ad) == set(retro.ROUTES))
    chk("fixture reproduces the dilution the change was made for",
        all(ad[r] < pr[r]["premium_pct"] for r in retro.ROUTES),
        "annual %s vs winter %s"
        % (ad, {r: pr[r]["premium_pct"] for r in retro.ROUTES}))

    # ordering: air-source draws hardest in the cold evening peak
    chk("air-source premium is the largest of the three",
        pr["ashp"]["premium_pct"] >= pr["shallow"]["premium_pct"]
        >= pr["network"]["premium_pct"],
        "%s / %s / %s" % (pr["ashp"]["premium_pct"],
                          pr["shallow"]["premium_pct"],
                          pr["network"]["premium_pct"]))

    print("\nwinter   %s" % {r: pr[r]["premium_pct"] for r in retro.ROUTES})
    print("annual   %s" % ad)
    print("hours    %s at mean GBP %s/MWh"
          % (hrs, pr["ashp"]["mean_price_gbp_mwh"]))
    print("\n%s" % ("all passed" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
