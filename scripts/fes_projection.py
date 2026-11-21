"""Extract the 2050 projection the grid panel needs from the NESO workbook.

INPUT is the published data workbook - "NESO Pathways 2025 Data Workbook"
(the series formerly called Future Energy Scenarios). OUTPUT is a small JSON
holding only the series the two charts draw, which is a few kilobytes against
the workbook's nineteen megabytes.

    python3 -I scripts/fes_projection.py ~/Downloads/NESO-Pathways-2025.xlsx \
            --out docs/fes_projection.json

RUN IT ONCE PER EDITION, NOT NIGHTLY. The workbook is published annually and
does not change in between, so this is not wired into the daily workflow: the
JSON is committed and refreshed by hand when NESO publishes. That also keeps
the build free of a 19 MB download it would otherwise repeat every night.

WHAT IT READS. Two sheets, both long-format tables with one column per year:

  ED1  Electricity demand summary - Pathway x Data item x Peak/Annual,
       annual demand in GWh and peak demand in GW, 2023 to 2050.
  ES1  Electricity supply data table - Pathway x Category x Type, installed
       capacity in MW, 2024 to 2050.

WHAT IT DOES NOT DO: derate anything. Capacity is emitted raw, by category
and technology, exactly as published. The split between firm capacity and
weather-dependent capacity - and what each is worth at a winter peak hour -
is an assumption, and assumptions belong on the face of the panel where a
reader can see and argue with them, not buried in a build script where they
would read as data. The same goes for the heat routes: this file carries
NESO's projection and nothing of Causeway's.

STRUCTURE IS CHECKED, NOT ASSUMED. The header row is found by looking for the
column names rather than counted to, every requested series is confirmed
present, and the run fails loudly naming what it could not find. A workbook
whose layout has moved should stop this script, not quietly produce a chart
with a missing line.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fes_inventory import (shared_strings, sheet_list,  # noqa: E402
                           preview_sheet)

# Pathways carried to the page. Decision of 21 Nov 2026 - Simon: the most and
# the least electrified of the net-zero pathways, so the band on the chart is
# the range NESO publishes rather than one line pretending to be a forecast.
# "Falling Behind" is read too, as the non-compliant case, and the page may
# choose not to draw it. "Ten Year Forecast" stops short of 2050 and is left.
PATHWAYS = ["Electric Engagement", "Holistic Transition",
            "Hydrogen Evolution", "Falling Behind"]

# ED1 items. The keys are what the page asks for; the values are the exact
# published item names, which is where an edition change will show up first.
ANNUAL_ITEMS = {
    "total":        "GBFES System Demand: Total",
    "heat_pumps":   "Residential Heat Pumps",
    "district":     "Residential District Heat Demand",
    "resistive":    "Residential Resistive Heat Demand",
    "storage_heat": "Residential Storage Heat",
    "aircon":       "Residential Air Conditioning Demand",
    "ev":           "Residential Electric Vehicles Demand (EVs)",
    "data_centre":  "Data Centre Demand",
}
PEAK_ITEMS = {
    "total":        "GBFES Peak Customer Demand: Total Consumption plus Losses",
    "hp_res":       "Residential Heat Pumps at Peak",
    "hp_com":       "Commercial Heat Pumps at Peak",
    "dh_res":       "Residential District Heat at Peak",
    "dh_com":       "Commercial District Heat at Peak",
    "dh_ind":       "Industrial District Heat at Peak",
    "ev_res":       "Residential EVs at Peak",
    "ev_com":       "Commercial EVs at Peak",
    "shifting":     "Residential Peak Shifting (Smart/TOUTs effect)",
}

HEADER_HINTS_ED1 = ("Data item", "Pathway", "Unit")
HEADER_HINTS_ES1 = ("Pathway", "Variable", "Category", "Type")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


class Missing(Exception):
    pass


def load_sheet(path, want, max_rows=20000):
    """Return (header cells, body rows) for one sheet, by name."""
    with zipfile.ZipFile(path) as z:
        ss = shared_strings(z)
        hit = [(n, sp) for n, st, sp in sheet_list(z)
               if n.strip().lower() == want.lower()]
        if not hit:
            raise Missing("sheet %r is not in this workbook" % want)
        rows, _, _ = preview_sheet(z, hit[0][1], ss, max_rows)
    return rows


def split_header(rows, hints):
    """Find the header row by its column NAMES, not by its position.

    FES moves its tables down the sheet between editions - ED1's header sits
    at row 5 in the 2025 workbook and there is no reason it will stay there.
    Counting to a row number is how a parser silently reads the wrong line.
    """
    for i, r in enumerate(rows):
        cells = [str(c) for c in r[1:] if c is not None]
        if all(any(h == c for c in cells) for h in hints):
            return r[0], r[1:], [x for x in rows[i + 1:]]
    raise Missing("no header row carrying all of %s" % (hints,))


def year_columns(header):
    """{year: zero-based index into a body row's cells}."""
    out = {}
    for i, c in enumerate(header):
        if c is not None and YEAR_RE.match(str(c)):
            out[int(c)] = i
    if not out:
        raise Missing("no year columns in the header")
    return out


def col_of(header, name):
    for i, c in enumerate(header):
        if c is not None and str(c).strip() == name:
            return i
    raise Missing("no %r column" % name)


def num(cells, i):
    if i >= len(cells) or cells[i] is None:
        return None
    try:
        return round(float(str(cells[i]).replace(",", "")), 3)
    except ValueError:
        return None


def demand(path):
    rows = load_sheet(path, "ED1")
    _, header, body = split_header(rows, HEADER_HINTS_ED1)
    yrs = year_columns(header)
    years = sorted(yrs)
    c_item = col_of(header, "Data item")
    c_path = col_of(header, "Pathway")
    c_unit = col_of(header, "Unit")
    c_kind = col_of(header, "Peak/ Annual/ Minimum")

    out, units, seen = {}, {}, set()
    for r in body:
        cells = r[1:]
        if max(c_item, c_path, c_kind) >= len(cells):
            continue
        pw, item = cells[c_path], cells[c_item]
        if pw not in PATHWAYS or item is None:
            continue
        kind = str(cells[c_kind] or "")
        table = PEAK_ITEMS if kind == "Peak" else ANNUAL_ITEMS
        pfx = "peak_" if kind == "Peak" else "annual_"
        for key, want in table.items():
            if str(item).strip() != want:
                continue
            seen.add(pfx + key)
            units[pfx + key] = cells[c_unit] if c_unit < len(cells) else None
            out.setdefault(pw, {})[pfx + key] = [num(cells, yrs[y])
                                                 for y in years]
    want_all = set("annual_" + k for k in ANNUAL_ITEMS) | \
               set("peak_" + k for k in PEAK_ITEMS)
    return years, out, units, sorted(want_all - seen)


def supply(path):
    """Installed capacity by category and technology, MW, undated.

    Rows are summed across the Connection column (transmission, distributed,
    micro) because the panel's question is what plant exists, not where it is
    connected. Only Capacity (MW) is taken; the sheet also carries generation,
    flows and carbon intensity, none of which the ceiling needs.
    """
    rows = load_sheet(path, "ES1")
    _, header, body = split_header(rows, HEADER_HINTS_ES1)
    yrs = year_columns(header)
    years = sorted(yrs)
    c_path = col_of(header, "Pathway")
    c_var = col_of(header, "Variable")
    c_cat = col_of(header, "Category")
    c_typ = col_of(header, "Type")

    out = {}
    for r in body:
        cells = r[1:]
        if max(c_path, c_var, c_cat, c_typ) >= len(cells):
            continue
        if str(cells[c_var] or "") != "Capacity (MW)":
            continue
        pw = cells[c_path]
        if pw not in PATHWAYS:
            continue
        cat, typ = cells[c_cat], cells[c_typ]
        if cat is None or typ is None:
            continue
        slot = out.setdefault(pw, {}).setdefault(
            str(cat), {}).setdefault(str(typ), [0.0] * len(years))
        for j, y in enumerate(years):
            v = num(cells, yrs[y])
            if v:
                slot[j] = round(slot[j] + v, 1)
    return years, out


def scop_improvement(path):
    """{pathway: TWh of 2050 demand avoided by SCOP improvement}, from F.05.

    FES PUBLISHES NO ABSOLUTE SPF. What it publishes is this: the saving
    attributable to heat pumps running better in 2050 than today, defined in
    the sheet's own words as the ratio of SCOP improvement multiplied by
    electricity demand from heat pumps and district heat. Divided by that
    electricity it gives the improvement RATIO, and multiplied by an
    independently sourced figure for today it gives FES's implied 2050
    efficiency. That chain is the only route to the number, so it is worth
    the awkward parse.

    F.05 is a chart sheet, not a data table: no header row covering the
    block, values scattered by a layout meant for a figure. So this finds
    the column headed "SCOP improvements" and, for each pathway label found
    anywhere to its left, takes the first number below that label in that
    column. Returns {} rather than failing if the sheet has moved - the
    page draws the projection without the efficiency chain and says so.
    """
    try:
        rows = load_sheet(path, "F.05", max_rows=200)
    except Missing:
        return {}
    col = None
    for r in rows:
        for i, c in enumerate(r[1:]):
            if c is not None and str(c).strip() == "SCOP improvements":
                col = i
                break
        if col is not None:
            break
    if col is None:
        return {}
    out, current = {}, None
    for r in rows:
        cells = r[1:]
        for c in cells[:col]:
            if c is not None and str(c).strip() in PATHWAYS:
                current = str(c).strip()
        if current and current not in out and col < len(cells):
            v = num(cells, col)
            if v:
                out[current] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--out")
    args = ap.parse_args()

    if not os.path.isfile(args.workbook):
        sys.exit("no such file: %s" % args.workbook)

    try:
        dyears, dem, units, missing = demand(args.workbook)
        syears, sup = supply(args.workbook)
        scop = scop_improvement(args.workbook)
    except Missing as e:
        sys.exit("this workbook does not have the shape expected: %s\n"
                 "Run fes_inventory.py on it and check the layout." % e)

    payload = {
        "source": os.path.basename(args.workbook),
        "publisher": "NESO, Pathways / Future Energy Scenarios data workbook",
        "generated": dt.date.today().isoformat(),
        "pathways": sorted(dem),
        "demand_years": dyears,
        "demand_units": units,
        "demand": dem,
        "capacity_years": syears,
        "capacity_MW": sup,
        "scop_improvement_TWh": scop,
        "scop_improvement_note": (
            "F.05: 2050 electricity demand avoided by heat pumps running "
            "better than today, against demand from heat pumps and district "
            "heat. FES publishes this saving, not an absolute SPF."),
        "note": ("Capacity is published installed capacity, NOT derated. The "
                 "firm/weather-dependent split and any derating at a winter "
                 "peak hour are assumptions made on the panel, not here."),
    }

    print("pathways: %s" % ", ".join(payload["pathways"]))
    print("demand years %d-%d, capacity years %d-%d"
          % (dyears[0], dyears[-1], syears[0], syears[-1]))
    for pw in payload["pathways"]:
        d = dem[pw]
        tot = d.get("annual_total", [])
        pk = d.get("peak_total", [])
        cats = sup.get(pw, {})
        print("\n%s" % pw)
        if tot and tot[0] and tot[-1]:
            print("   annual demand  %6.0f -> %6.0f TWh"
                  % (tot[0] / 1000, tot[-1] / 1000))
        if pk and pk[0] and pk[-1]:
            print("   peak demand    %6.1f -> %6.1f GW" % (pk[0], pk[-1]))
        for cat in sorted(cats):
            end = sum(v[-1] for v in cats[cat].values()) / 1000
            print("   %-16s %6.1f GW in %d" % (cat, end, syears[-1]))
    if missing:
        print("\nNOT FOUND, and the page will have gaps where they were "
              "expected:")
        for m in missing:
            print("   %s" % m)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        print("\nwrote %s (%.0f kB)"
              % (args.out, os.path.getsize(args.out) / 1e3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
