#!/usr/bin/env python3
"""Summarise the DESNZ postcode-level electricity CSVs to the numbers needed.

The published files are large - the 2024 all-domestic file is about 76 MB -
but the question they answer here is small: what share of domestic meters and
of domestic consumption sits on Economy 7, and how does that vary by region?

This streams the CSVs rather than loading them, so memory use is trivial and a
76 MB file takes seconds. No dependencies beyond the standard library.

WHY THIS MATTERS FOR UK HEAT SPLIT
----------------------------------
The site's capacity headroom test credits the 20% what-if with displacing a
fifth of existing resistive space heating, and shapes that displaced load like
SPACE HEAT - following degree-hours, and therefore present at the winter peak.

If a material share of the resistive fleet is on Economy 7, that is wrong: the
load ran between midnight and seven and there is nothing to displace at the
peak. On the current record the binding hour falls at NOON, where an Economy 7
credit is exactly zero, so the whole 1.35 GW credit is at stake.

The DESNZ meter share is the first of two inputs. The second is the Elexon
Profile Class 2 shape, which is separated into switched and base load and
gives the half-hourly profile rather than an annual total.

A CAUTION THE SOURCE ITSELF RAISES
----------------------------------
An Economy 7 meter permits a two-rate tariff; it does not compel one, and in
most cases a single meter records both the ordinary and the off-peak
consumption. So a meter count is an UPPER BOUND on genuinely night-shifted
load, and the consumption split is the better of the two figures.

Note also that the E7 share of DOMESTIC ELECTRICITY is not the E7 share of
RESISTIVE HEATING. Households have Economy 7 because they heat electrically,
so the share of resistive heat on a night tariff is higher - probably much
higher - than the share of domestic electricity. This script gives the first;
the second needs the two to be crossed, for which the off-gas-grid columns of
the subnational tables are the obvious lever.

USAGE
-----
    python3 e7_share.py all_meters.csv [economy7.csv] [standard.csv]

Any subset works. Column names are matched case-insensitively on substrings,
because DESNZ has renamed them between vintages.

    python3 e7_share.py --selftest

Causeway Energies, 20 August 2026. Offline: not on the daily build path.
"""

import csv
import io
import os
import sys

# Column-name fragments, lowercased. DESNZ has used several spellings.
METER_KEYS = ("num_meters", "number of meters", "meters", "num_of_meters")
CONS_KEYS = ("total_cons", "total consumption", "consumption_kwh", "total_kwh")
AREA_KEYS = ("region", "country", "la_name", "local authority", "msoa", "lsoa")
PC_KEYS = ("postcode", "pcd")


def _pick(header, keys):
    """First column whose lowercased name contains any key. None if absent."""
    low = [h.lower().strip() for h in header]
    for k in keys:
        for i, h in enumerate(low):
            if k in h:
                return i
    return None


def summarise(path, label):
    size = os.path.getsize(path) / 1e6
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        r = csv.reader(f)
        header = next(r)
        mi = _pick(header, METER_KEYS)
        ci = _pick(header, CONS_KEYS)
        ai = _pick(header, AREA_KEYS)
        pi = _pick(header, PC_KEYS)
        if mi is None and ci is None:
            print("  %s: no meter or consumption column found." % label)
            print("     header was: %s" % ", ".join(header[:12]))
            return None
        # SUBTOTAL ROWS. Each outcode carries an "All postcodes" summary row
        # followed by its individual postcodes, so summing every row counts
        # each meter twice - the 2024 all-domestic file totals 57.1m meters
        # and 192 TWh against a GB reality of roughly 29m and 95 TWh. Skipped
        # here, and counted separately so the two can be cross-checked.
        meters = cons = rows = 0
        sub_m = sub_c = sub_n = 0
        by_area = {}
        for row in r:
            if len(row) <= max(x for x in (mi, ci) if x is not None):
                continue
            is_sub = (pi is not None and pi < len(row)
                      and row[pi].strip().lower().startswith("all postcode"))
            m = c = 0.0
            try:
                if mi is not None and row[mi].strip():
                    m = float(row[mi].replace(",", ""))
                if ci is not None and row[ci].strip():
                    c = float(row[ci].replace(",", ""))
            except ValueError:
                continue
            if is_sub:
                sub_m += m; sub_c += c; sub_n += 1
                continue          # subtotal, not a postcode
            rows += 1
            meters += m
            cons += c
            if ai is not None and ai < len(row):
                a = row[ai].strip()
                if a:
                    t = by_area.setdefault(a, [0.0, 0.0])
                    t[0] += m
                    t[1] += c
    print("\n%s  (%s, %.0f MB)" % (label, os.path.basename(path), size))
    print("  postcode rows %17s" % format(rows, ","))
    if sub_n:
        agree = (abs(sub_m - meters) / meters * 100) if meters else 0
        print("  subtotal rows %17s  skipped (they duplicate the detail)"
              % format(sub_n, ","))
        print("  cross-check: subtotals sum to %s meters, detail to %s"
              % (format(int(sub_m), ","), format(int(meters), ",")))
        print("               difference %.3f%% - should be ~0" % agree)
    print("  meters       %18s" % format(int(meters), ","))
    print("  consumption  %18s kWh   = %.2f TWh" % (format(int(cons), ","),
                                                    cons / 1e9))
    if meters:
        print("  mean         %18.0f kWh/meter" % (cons / meters))
    if pi is None:
        print("  (no postcode column - this may be a regional file)")
    return {"meters": meters, "cons": cons, "by_area": by_area, "label": label}


def compare(all_r, e7_r, std_r):
    print("\n" + "=" * 62)
    print("ECONOMY 7 SHARE")
    print("=" * 62)
    if e7_r is None and std_r is not None and all_r is not None:
        e7_r = {"meters": all_r["meters"] - std_r["meters"],
                "cons": all_r["cons"] - std_r["cons"],
                "by_area": {}, "label": "Economy 7 (derived: all - standard)"}
        print("\n  Economy 7 derived by difference.")
    if e7_r is None or all_r is None:
        print("\n  Need the all-domestic file plus either the Economy 7 file")
        print("  or the standard file to compute a share.")
        return
    mp = 100 * e7_r["meters"] / all_r["meters"] if all_r["meters"] else 0
    cp = 100 * e7_r["cons"] / all_r["cons"] if all_r["cons"] else 0
    print("\n  %-34s %8.1f%%" % ("of domestic METERS", mp))
    print("  %-34s %8.1f%%   <- the better figure" % ("of domestic CONSUMPTION", cp))
    print("\n  Economy 7 consumption: %.2f TWh of %.2f TWh domestic"
          % (e7_r["cons"] / 1e9, all_r["cons"] / 1e9))
    if e7_r["meters"] and all_r["meters"]:
        me = e7_r["cons"] / e7_r["meters"]
        ma = all_r["cons"] / all_r["meters"]
        print("\n  mean per Economy 7 meter  %7.0f kWh/yr" % me)
        print("  mean per domestic meter   %7.0f kWh/yr" % ma)
        print("  ratio                     %7.2f" % (me / ma if ma else 0))
        print("\n  A ratio well above 1 is the tell that Economy 7 households")
        print("  are the electrically HEATED ones - which is why the E7 share")
        print("  of resistive heat is higher than its share of electricity.")
    if e7_r.get("by_area") and all_r.get("by_area"):
        rows = []
        for a, (m, c) in all_r["by_area"].items():
            e = e7_r["by_area"].get(a)
            if e and c:
                rows.append((100 * e[1] / c, a, c / 1e9))
        if rows:
            rows.sort(reverse=True)
            print("\n  BY AREA, Economy 7 share of consumption:")
            for pct, a, tw in rows[:6]:
                print("    %-34s %6.1f%%   (%.2f TWh)" % (a[:34], pct, tw))
            if len(rows) > 8:
                print("    ...")
                for pct, a, tw in rows[-2:]:
                    print("    %-34s %6.1f%%   (%.2f TWh)" % (a[:34], pct, tw))


def selftest():
    print("SELF-TEST - synthetic files, no download needed")
    hdr = "Postcode,Region,Num_meters,Total_cons_kWh\n"
    allf = io.StringIO(hdr + "AB1 1AA,North,100,400000\nBB2 2BB,South,100,300000\n")
    e7f = io.StringIO(hdr + "AB1 1AA,North,25,150000\nBB2 2BB,South,10,50000\n")
    for name, buf in (("all.csv", allf), ("e7.csv", e7f)):
        with open("/tmp/" + name, "w") as f:
            f.write(buf.getvalue())
    a = summarise("/tmp/all.csv", "ALL DOMESTIC")
    e = summarise("/tmp/e7.csv", "ECONOMY 7")
    compare(a, e, None)
    print("\n  expected: 17.5% of meters, 28.6% of consumption, ratio 1.63")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    files = [a for a in argv[1:] if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    res = []
    for i, p in enumerate(files):
        if not os.path.exists(p):
            print("not found: %s" % p)
            return 1
        lab = ("ALL DOMESTIC", "ECONOMY 7", "STANDARD")[i] if i < 3 else p
        res.append(summarise(p, lab))
    while len(res) < 3:
        res.append(None)
    compare(res[0], res[1], res[2])
    print("\nNext: the Elexon Profile Class 2 shape, which is separated into")
    print("switched and base load and gives the half-hourly profile rather")
    print("than an annual total. Meter share alone cannot say WHEN the load")
    print("ran, and when is the whole question.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
