"""Build data.json: fetch feeds, regress LDZ (buildings) gas on HDD, compute
the weekly GB heat & cooling mix, write output.

Gas space heating: live regression (LDZ offtake vs HDD).
Other fuels & cooling: level from ECUK 2025 End Use tables (calendar 2024,
UK, rev. 20 Apr 2026), weekly shape from HDD (heating) / CDD base 18
(cooling); DHW components flat. Cooling & ventilation split 50% flat
ventilation / 50% CDD-shaped cooling (stated assumption).

Calibration anchor: ECUK U3 domestic gas space heating 189.6 TWh + U5
services gas heating 68.5 TWh = 258.1 TWh, GB-adjusted, weather-normalised.

Phase-1 ticker (Jul 2026): the weekly hero estimators live in
compute_week()/compute_week_emissions() and are used BOTH for the live
panels and for the rolling weekly history array that feeds the sparklines.
History weeks are calendar weeks (Mon-Sun), computed only where the gas
feed actually served all 7 days ("live" by construction), priced at the
Ofgem cap in force that week, with that week's historical grid carbon
intensity. Idempotent: keyed by week_ending; the 2 most recent weeks are
recomputed each run (feed revisions), older weeks are frozen as written.
"""

import datetime as dt
import json
import math
import os
import sys
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from fetch_degree_days import fetch_degree_days, HDD_BASES  # noqa: E402
from fetch_gas import fetch_gas_demand, fetch_gas_sap_series                       # noqa: E402
from fetch_prices import fetch_gas_sap, fetch_elec_mid        # noqa: E402
from fetch_carbon import fetch_carbon_intensity               # noqa: E402
from fetch_electricity import fetch_daily_underlying_demand   # noqa: E402
import retro                                                  # noqa: E402
# fetch_odh retired 10 Aug 2026 - the comfort deficit now reads the site's own
# daily-mean cooling degree days rather than a separate hourly 26 C count.

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
# Weekly HISTORY span. NOT a year - and that distinction bit hard on 11 Aug
# 2026: raising this 365 -> 730 doubled every quantity that used the
# regression window as if it were a trailing twelve months. hdd_trailing_12m
# went 2,017 -> 4,114, the calibrated annual heat went 261.8 -> 531.5 TWh, and
# the electrification limit HALVED (network 98% -> 49%) because its denominator
# doubled. Anything annual must use ANNUAL_DAYS, never this constant.
#
# AND IT IS NOT A RETENTION KNOB. Raising it does not make the site keep more
# history; it enlarges the REGRESSION SAMPLE and refits the model. Retention
# is HISTORY_MAX below. Confused once already - see the 11 Aug incident above.
WINDOW_DAYS = 730          # regression sample, NOT a retention cap
ANNUAL_DAYS = 365          # a year, for every trailing-12-month quantity
EST = " \u2020"   # marks a Causeway estimate - see site footnote
# Weekly history retention. Was 120 weeks; RETIRED 20 Nov 2026, set to None
# meaning no cap, so no week is ever rolled off.
#
# This was the worst of the three caps, because of what it discarded. It had
# been sitting AT 120 - dropping a week every week - and each dropped week took
# with it that week's bill at THAT week's price cap and that week's emissions at
# THAT week's grid carbon intensity.
#
# Of those two, only ONE was ever at risk, and it is worth being exact because
# the first version of this comment got it wrong. The price cap is CAP_HISTORY
# above - a table in this file, under version control, so it was never lost.
# Grid CI was the real loss: it was held only in retro.json's rolling hours and
# nowhere daily. That is why the archive was extended on 20 Nov 2026 to carry
# ci_g_kwh, gas and the canonical degree-day temperature, which together with
# CAP_HISTORY make a rolled-off week rebuildable for any week the archive
# covers. Weeks before the archive's CI coverage begins are not.
#
# This is the same fault the daily archive was built to fix on 22 Oct 2026,
# when a pinned coefficient could not be reproduced because retro.json had
# trimmed the days it was fitted on. The lesson was not carried across to
# history at the time. It is now.
#
# Safe: weeks outside the regression window are already handled - they keep
# their stored value and are stated as unrecomputable (see the restate block).
# Retention does not refit anything.
#
# Cost: 852 bytes per week in data.json, which the page DOES download - about
# 44 KB a year, +117 KB at five years on a 602 KB payload. Paid deliberately.
HISTORY_MAX = None        # no cap - no week is rolled off; see above

# --- retro span -------------------------------------------------------------
# How far back the hourly store reaches. 796 days spans two winters, so the
# worst hour and the binding hour are chosen ACROSS winters rather than
# describing the only one on record.
#
# TWO-STEP, deliberately. RETRO_BACKFILL stays False for the first run after
# raising the span: the cap change alone lets the store stop shrinking and
# grow forward, which proves the plumbing without a refetch. Set it True for
# ONE run to pull the earlier winter in, then set it back to False so the
# store returns to cheap two-day increments.
#
# WHAT A BACKFILL DOES AND DOES NOT MOVE. Corrected 20 Nov 2026 - the version
# of this note that stood until then said "a colder hour anywhere in the new
# span becomes the worst hour, and the binding hour, headroom and
# electrification limit all follow it". That was wrong about the limit, and it
# was quoted to the client as a reason to expect the headlines to move. Read
# the code, not this comment, when it matters: retro.py confines BOTH its
# searches to the trailing 8,760 hours (s0 = max(0, n - 8760) at retro.py:887
# for the worst hour and :2028 for the limit), so a deeper store alone changes
# neither. Widening them is a separate, deliberate edit.
#
#   - The WORST HOUR search is max(heat[i]) - modelled heat only, no fleet
#     figure in it. Widening it is free of new data, and it DOES move the
#     published stress cards, because they describe that hour.
#   - The ELECTRIFICATION LIMIT needs each winter's de-rated dispatchable
#     block. DISPATCH_BY_WINTER holds 2024/25 and 2025/26 only, and
#     dispatch_block() used to fall back to the LATEST winter's figure without
#     saying so. Backfilling three more winters would have tested them against
#     a fleet that did not exist. The fallback is now explicit; see retro.py.
#
# AND ONE THING THAT STILL DOES NOT REACH BACK: hourly heat is shaped from the
# CURRENT annual anchor (the shaping normalises on the trailing 365, so the
# anchor is not diluted - but an older winter is expressed on today's demand
# basis, not its own). That caveat lands squarely on any widened worst hour.
# 1826 puts the retro store's start on 2021-11-18, the day the daily archive
# begins, so the whole site dates from one day rather than two. All four
# hourly feeds were proven at that date by the five-feed probe on 19 Nov 2026.
RETRO_SPAN_DAYS = 1826
# SET BACK TO False AFTER ONE SUCCESSFUL RUN. Not because leaving it on
# refetches daily - it does not. r_start is min(want, prev start_day), and
# `want` only creeps forward, so once the store starts on the wanted day the
# start matches, build_retro takes its incremental path and keeps everything
# but the two-day revision window. The reason to turn it off is that it ARMS
# a full refetch to fire unattended: raise RETRO_SPAN_DAYS again with this
# flag still True and the next nightly build silently refetches ~1,830 days
# from four feeds, which is how a scheduled run times out at 3am.
RETRO_BACKFILL = True
RETRO_FETCHERS = {}   # tests inject {'fetch_temp':..,'fetch_mid':..,'fetch_ci':..}  # weekly entries kept (spec: cap and roll)


def _recency(status, last_good, lag_ok_days=7):
    """Downgrade an 'ok' source to 'lagging' if last_good is old.
    Fetch health and data recency are different facts."""
    if status == "ok" and last_good:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(
                str(last_good)[:10])).days
            if age > lag_ok_days:
                return "lagging"
        except ValueError:
            pass
    return status
# Cooling shape base, on the DAILY MEAN. Moved 18.0 -> 15.5 on 11 Aug 2026.
# 15.5 degC is the balance point at which comfort cooling begins in UK
# non-domestic buildings - the temperature at which internal and solar gains
# offset losses at the cooling setpoint. High internal gains put it well below
# any climate-comparison convention: the Met Office 22 degC CDD base is a
# climate statistic, not a building balance point, and would understate
# operation badly. 15.5 is the traditional UK degree-day base, is what CIBSE
# TM46 applies across all sectors. Band 12-18 degC.
#
# THE REGRESSION NO LONGER CORROBORATES IT, and this comment used to say it
# did. That claim rested on an undemeaned fit putting the best-fit balance
# point at 14.5 degC. The fit was re-derived on 22 Oct 2026 and the pin now
# says 9.75 degC with a profile interval of 7.75-11.25, which does not reach
# 15.5. The base is NOT moved on that account: a balance point fitted to
# national demand and a building-physics base are not the same quantity, and
# the fit's threshold trades off against its slope along a ridge. But 15.5
# now stands on TM46 and the degree-day convention alone, and the tension is
# stated on the panel rather than left in a comment. Withdrawn 19 Nov 2026.
COOL_BASE = "15.5"
# Base for the tier-3 comfort deficit, on the DAILY MEAN. Corrected 10 Aug
# 2026: the deficit previously counted hours of outdoor air above 26 C, but
# 26 C is CIBSE's INDOOR operative criterion (TM59 Criterion B, bedrooms, 1%
# of night hours) and is not an outdoor trigger. Applied to outdoor air it
# found 197 hours a year where the standard fails a bedroom at 33 hours
# indoors, so the deficit was understated roughly five-fold.
# 20 C is derived from the 2017 Energy Follow-Up Survey temperature
# sensitivity regression (Loughborough for DESNZ, 2024): indoor rises ~0.48 C
# per 1 C outdoor in bedrooms on a two-day mean of daily maxima, with a
# one-to-two day thermal-mass lag, so bedrooms begin breaching 26 C indoors
# when outdoor daily means reach roughly 19-22 C. Band 18-22 C.
# NOT YET IN THE NUMBERS: a night-time urban heat island uplift. Bedroom
# overheating is a night phenomenon and city night minima run 3-4 C above
# their surroundings, which a population-weighted ERA5 series does not
# resolve; on this record that would add a further 10-20%.
# NO FALLBACK BASE. 18 and 22 are the band ENDS, not substitutes for the
# centre: on a mild week base 22 returns exactly zero degree-days while base
# 18 returns 238 degree-hours, so falling back to either misrepresents rather
# than degrades. If the base is missing the tier goes unavailable and the
# previous value stands, which is the site's existing pattern and makes the
# one-line addition to fetch_degree_days unmissable.
DEFICIT_BASE = "20.0"

ECUK_UK_GAS_SPACE_HEAT_TWH_2024 = 258.1
# Retained for the gas space-heat calibration anchor only, and now sourced to
# the same NISRA figures as the per-fuel table below rather than sitting at a
# slightly different value.
GB_SHARE_OF_UK_GAS_HEAT = 0.988   # NI not on the GB LDZ system; estimate

# PER-FUEL GB SHARES, added 17 Aug 2026 after peer review, and revised the
# same day when the first derivation proved too aggressive.
#
# Until now the gas-specific 0.985 above was applied to EVERY fuel and end use
# when scaling the UK ECUK annuals to GB. That factor is a statement about the
# GAS NETWORK - Northern Ireland is not on the GB LDZ system - and it does not
# transfer to a fuel whose NI penetration is nothing like its GB penetration.
#
# OIL, the material case. Two independent routes, reconciled:
#   NIHE/BRE "Energy consumption in NI's housing stock: 2016" (BREDEM on the
#   House Condition Survey) gives NI domestic primary space heating 10,700 GWh
#   with oil at 76%, and water heating 3,500 GWh with oil at 2,700 - so 10.8
#   TWh of NI oil heat on a 2016 basis, 21% of the UK's 51.0 TWh.
#   The Irish Heat Split sibling estimates 8.3 TWh on a trailing-twelve-month
#   basis, 16%.
# Those are not in conflict: they are nine years apart in the one jurisdiction
# where oil is retreating fastest. NISRA puts the oil share of NI homes at 61%
# in 2024/25 against 68% on the 2016 basis, and nine years of fabric and boiler
# improvement plus the weather difference between a trailing window and the
# 2016 year closes the rest - 10.8 x 0.90 x 0.93 x 0.97 = 8.8 against 8.3, a
# 5% residual.
#
# The site reports a trailing window, so the current-basis figure is the right
# one: NI at about 17% of UK oil heat, GB share 0.83. A FIRST attempt used 0.69,
# derived from household counts alone (61% of 0.80m NI homes against ~1.1m GB
# oil homes), which ignored consumption per household and overstated the
# correction - NI homes are smaller and newer than the GB off-grid stock, which
# skews rural and large.
#
# Two known biases, opposed: the oil share is still falling, which argues
# lower; and NIHE covers DOMESTIC only, so NI non-domestic oil sits on top,
# which argues higher.
#
# Other fuels, all dagger: NI ~0.80m households of ~28.4m UK (2021 census).
#   gas    NISRA 36% of NI homes; GB gas-connected ~23m      -> 0.99
#   solid  NI low as primary, higher as secondary            -> 0.89
#   everything else: no NI-specific driver, so the household share -> 0.97
GB_SHARE = {
    "gas": 0.988,
    "oil": 0.830,
    "solid": 0.893,
    "default": 0.972,
}


def gb_share(key):
    """GB share of a UK annual, by fuel. See GB_SHARE above."""
    for k in ("oil", "solid"):
        if key.startswith(k):
            return GB_SHARE[k]
    if key.startswith("gas"):
        return GB_SHARE["gas"]
    return GB_SHARE["default"]
ECUK_ANCHOR_STATUS = ("ECUK 2025 U3+U5, calendar 2024, UK; GB share and "
                      "weather normalisation applied")

# UK TWh, ECUK 2025 End Use tables (calendar 2024). space = HDD-shaped,
# dhw/flat = constant, cool = 50% flat + 50% CDD-shaped.
ANNUAL_TWH = {
    "gas_dhw":       64.7,   # U3 55.6 + U5 hot water 9.1
    "elec_space":    21.2,   # U3 13.7 + U5 heating 7.5
    "elec_dhw":       5.4,   # U3 3.8 + U5 1.6
    "oil_space":     45.7,   # U3 21.9 + U5 23.8
    "oil_dhw":        5.3,
    "bio_space":     23.8,   # U3 bio 12.1 + U5 'other' 11.7
    "bio_dhw":        3.5,
    "heat_networks":  6.2,   # U3 3.2 + U5 3.0
    "solid":          2.0,
    "cooling_vent":  10.4,   # U5 cooling & ventilation electricity
}
# DO NOT TURN cooling_vent INTO AN ANNUAL SERIES. Tested 21 Nov 2026 against
# ECUK 2026 and ECUK 2025 End Use tables, Table U5, because the obvious fix for
# a cooling panel that cannot show growth was to re-level Tier 1 on ECUK each
# year instead of holding one anchor. It does not work, and the reason is
# arithmetic rather than judgement.
#
# ECUK's services end-use split comes from BEES, a survey run ONCE, in 2015.
# The methodology note says services consumption "is separated into end uses
# according to the proportions detailed below", and contrasts this with the
# domestic sector, whose assumptions "are updated each year". So the cooling
# line is a FIXED SHARE of total services electricity:
#
#   cooling & ventilation / all services electricity
#     = 13.4085% in 2021, 2022, 2023, 2024 and 2025 (ECUK 2026)
#     = 13.4085% in 2017 through 2024              (ECUK 2025)
#   per sub-sector too: Health 23.009%, Offices 18.852%, Retail 8.333%,
#   Storage 5.420% - constant in every year of both editions.
#
# The year-on-year change in the cooling line is therefore IDENTICAL, to the
# decimal, to the year-on-year change in services electricity: -1.8, +1.2,
# -0.4, +1.3 per cent. In 2020 it "falls 15.7%", which is lockdown emptying
# offices. Shaping Tier 1 to this series would shape it to the services
# denominator and present the result as cooling demand.
#
# And the restatement would be louder than the signal. The same year moves
# MORE between editions than between years:
#   2021 10,665 -> 11,107 GWh (+4.1%)   2023 10,439 -> 11,039 (+5.7%)
#   2022 10,368 -> 10,911     (+5.2%)   2024 10,447 -> 10,998 (+5.3%)
# Largest year-to-year move inside ECUK 2026: 1.8%. Largest same-year revision
# between editions: 5.7%. Re-anchoring annually would redraw bars for years
# already published, by more than the movement being claimed.
#
# NOTE for whoever re-anchors this constant: 10.4 is calendar 2024 from the
# ECUK 2025 edition. ECUK 2026 puts the SAME YEAR at 11.0 TWh. That is a 5.7%
# level change with no change in cooling, so the bump belongs in a run of its
# own where it can be seen, not folded into another change.
#
# WHAT WOULD ACTUALLY WORK is the method DESNZ itself uses where it has the
# inputs: Table U8 publishes heat-pump electricity annually, 2014-2025, and
# Note 5 says it is "estimated using a series of assumption on install base,
# average capacity and hours of operation". A cooling equivalent needs a UK
# cooling install base by year, which nothing currently wired in supplies.
# Until it exists, the tier series is weather at a FIXED FLEET, and the panel
# says so rather than implying growth. Decision: Simon, 21 Nov 2026.

# --- prices ------------------------------------------------------------------
# Ofgem price cap, GB direct-debit average unit rates incl VAT, p/kWh, by
# cap-period start date. Sourced: Ofgem quarterly announcements.
# EXTEND QUARTERLY (next: 1 Jan 2027 rates, announced by ~25 Nov 2026).
# NOTE for that row: electricity VAT is suspended only to 31 Mar 2027,
# so the January row is still a 0% VAT figure and the APRIL 2027 row
# returns to 5%. Check which basis Ofgem quotes before entering either.
# Ticker correctness rule 1: a backfilled week is priced at the cap IN FORCE
# that week, never today's. cap_prices() resolves the period from the date.
# If the live gas window ever reaches before the first row, extend backwards
# from Ofgem's historical cap levels before trusting those weeks' bills.
CAP_HISTORY = [
    # (period start, gas p/kWh, elec p/kWh) - Ofgem default tariff cap,
    # direct debit, GB average, incl VAT, from each quarter's own release.
    # Table extended back to 2024-04-01 on 10 Aug 2026 for the 24-month view.
    # 2024-04-01 IS THE FLOOR AND IT IS A REAL ONE: the Energy Price Guarantee
    # ran to 31 March 2024, so before that households did not pay the cap and
    # "each week priced at its own week's cap" stops being true. Extending
    # earlier needs a stated convention for the crisis years, not more rows.
    # Added 20 Nov 2026. Its absence left Jan-Mar 2024 to fall through to an
    # annual pre-cap row. Ofgem's announcement for the period gives 28.62
    # p/kWh electricity (53.35 p/day standing) and 7.42 p/kWh gas (29.60
    # p/day); their news page rounds these to 29p and 7p. CHECKED: at that
    # period's TDCVs - 2,700 kWh electricity, 11,500 gas - over 366 days,
    # those rates and standing charges reconstruct a typical bill of £1,930
    # against the £1,928 Ofgem published, so the pair is right.
    ("2024-01-01", 7.42, 28.62),
    ("2024-04-01", 6.04, 24.50),
    ("2024-07-01", 5.48, 22.36),
    ("2024-10-01", 6.24, 24.50),
    ("2025-01-01", 6.34, 24.86),
    ("2025-04-01", 6.99, 27.03),
    ("2025-07-01", 6.33, 25.73),
    ("2025-10-01", 6.29, 26.35),
    ("2026-01-01", 5.93, 27.69),
    ("2026-04-01", 5.74, 24.67),
    ("2026-07-01", 7.33, 26.11),
    # 1 Oct 2026: cap +3.6% to GBP 1,723 (Ofgem rounds to 4%), announced
    # 26 Aug 2026. Gas 7.33 -> 7.97 (+8.7%), electricity 26.11 -> 26.32.
    #
    # THE ELECTRICITY FIGURES ARE NOT LIKE FOR LIKE, and this is the one
    # quarter where the table's "incl VAT" convention needs saying out loud.
    # VAT on electricity is SUSPENDED from 1 Oct 2026 to 31 Mar 2027, so
    # July's 26.11 includes 5% and October's 26.32 includes 0%. Ex VAT the
    # move is 24.87 -> 26.32, an underlying rise of 5.8% against a quoted
    # 0.8%. Gas keeps its 5% throughout.
    #
    # The value stored is what a household PAYS, which is the convention of
    # this table and the right basis for a bill. Two consequences to expect
    # rather than debug: the electric routes will look almost flat this
    # quarter while their underlying cost rose 5.8%, and on 1 Apr 2027 when
    # VAT returns there will be a 5% step with no change in the underlying
    # price at all. That step is real for the bill and spurious for the
    # commodity, and the wholesale basis of the heat-price chart is the view
    # that is unaffected by either.
    ("2026-10-01", 7.97, 26.32),
]
# Oil/bio/heat-network/solid unit prices are flagged estimates, held flat
# across the history window (no reliable weekly series; noted in methods).
NONCAP_PRICES_P_PER_KWH = {
    "oil": 7.2,             # est. kerosene ~75p/l / 10.35 kWh/l - confirm
    "bio": 7.5,             # est. wood pellet - confirm
    "heat_networks": 10.0,  # est. typical network tariff - confirm
    "solid": 6.0,           # est.
}

# Non-domestic (services) unit prices for the sector-blended bill, p/kWh
# incl CCL - Causeway estimate anchored on DESNZ QEP non-domestic tables
# 3.4.1-3.4.2 (Q4 2025 manufacturing averages, excl CCL: gas 3.5 / elec
# 16.8, QEP Mar 2026 - the large-user floor; ECUK services buildings sit
# in the small/medium bands, several p/kWh higher; CCL 2025/26 adds
# 0.775 p/kWh to each). UPDATE ANNUALLY on the ECUK refresh from the QEP
# tables (gov.uk statistical-data-sets/gas-and-electricity-prices-in-the-
# non-domestic-sector). Held flat across the history window (no quarterly
# series attempted) - stated in history_note and methodology 9.2.
NONDOM_PRICES_P_PER_KWH = {"gas": 5.5, "elec": 24.0}   # estimates †
# RETAINED AS THE FALLBACK ONLY. Superseded 20 Nov 2026 by the dated series
# below; kept so a date outside that series still prices rather than throws.

# Non-domestic unit prices, QUARTERLY, p/kWh INCLUDING CCL.
# Source: DESNZ Quarterly Energy Prices table 3.4.2, "Average" column, UK.
#
# WHY THIS REPLACED A CONSTANT. The flat pair above was one 2025-anchored
# estimate applied across the WHOLE history window. Over the span below the
# real series runs gas 4.17-7.18 and electricity 17.73-28.39 - so the constant
# was within about a tenth across the two years already published, and out by
# up to 40% through the energy crisis. Backfilling history to 2022 without
# this would have made the non-domestic half of every crisis-era bill the
# largest error on the site.
#
# The "Average" column is used because that is what the old constant tracked:
# QEP 3.4.2 for Q4 2025 reads gas 5.07 / elec 23.99 against the stored
# 5.5 / 24.0. Note the site's own ECUK services buildings sit nearer the
# Small/Medium bands, which run several p/kWh higher (Q4 2025: 4.89 / 29.57);
# moving to those would be a change of basis, not a correction, so it is not
# made here.
NONDOM_PRICES_HISTORY = [
    # (period start, gas p/kWh, elec p/kWh)
    ("2021-10-01", 4.18, 17.73),
    ("2022-01-01", 4.17, 18.14),
    ("2022-04-01", 5.00, 19.33),
    ("2022-07-01", 6.74, 22.20),
    ("2022-10-01", 6.03, 24.80),
    ("2023-01-01", 7.18, 25.37),
    ("2023-04-01", 6.12, 26.88),
    ("2023-07-01", 5.94, 28.32),
    ("2023-10-01", 6.40, 28.39),
    ("2024-01-01", 6.12, 27.84),
    ("2024-04-01", 5.42, 25.99),
    ("2024-07-01", 5.35, 25.67),
    ("2024-10-01", 5.51, 25.35),
    ("2025-01-01", 5.54, 25.74),
    ("2025-04-01", 5.31, 24.03),
    ("2025-07-01", 5.13, 23.35),
    ("2025-10-01", 5.07, 23.99),
    ("2026-01-01", 5.16, 24.14),
    ("2026-04-01", 5.28, 24.51),
]

# Domestic prices BEFORE the cap table starts, p/kWh, ANNUAL.
# Source: DESNZ QEP tables 2.3.4 (gas) and 2.2.4 (electricity), direct debit
# average variable unit price, GB regions only (Northern Ireland excluded),
# unweighted mean of the 28 regions, converted from GBP/kWh.
#
# TWO THINGS ARE DIFFERENT ABOUT THESE ROWS AND BOTH ARE STATED ON THE PAGE.
#
# BASIS, AND WHY THESE ARE NOT RESCALED. QEP is the average actually PAID
# across all tariffs; CAP_HISTORY is the cap LEVEL. Different quantities, so
# the obvious worry is a step at the join. These rows were briefly multiplied
# by a factor of 1.0217/1.0229 derived from 2025, the only full overlap year
# the cap table then had.
#
# Adding the Jan-Mar 2024 cap row gave a second overlap year and killed that
# correction. Cap divided by QEP, two years, two weightings:
#
#               gas     elec
#     2024 simple   0.970   0.973
#     2024 winter   1.005   0.999
#     2025 simple   1.022   1.023
#     2025 winter   1.016   1.019
#
# The ratio moves 0.97-1.02 with the year and with how the quarters are
# weighted - a spread as large as the offset it was supposed to measure. So
# there is no stable basis difference to correct, and a factor fitted to one
# year on one weighting was fitting noise. The rows below are QEP as
# published. The two series agree to within about 3% with no consistent
# direction, which is the honest statement.
#
# RESOLUTION. These are ANNUAL. The cap moved twice within 2022 alone (+54%
# in April, then the EPG in October), and an annual mean cannot show that.
# Weekly energy still varies; the price within a year does not. The page says
# so on any window that reaches these weeks.
CAP_QEP_SCALE = {"gas": 1.0, "elec": 1.0}   # no offset found - see above
DOM_PRE_CAP_ANNUAL = [
    # (year start, gas p/kWh, elec p/kWh) - QEP annual, GB, direct debit,
    # as published. The cap table now reaches 2024-01-01, so these rows
    # govern 2023 and earlier only.
    ("2020-01-01", 3.46, 16.90),
    ("2021-01-01", 3.36, 18.68),
    ("2022-01-01", 7.25, 28.20),
    ("2023-01-01", 8.55, 30.25),
]

# Domestic share of each purchased-energy component, from the same ECUK
# 2025 U3 (domestic) / U5 (services) tables that anchor ANNUAL_TWH.
# gas_space: calibration anchor split 189.6 dom / 68.5 serv. oil, elec and
# bio combine space + DHW at annual weights (DHW assumed domestic where U5
# has no line). cooling_vent is U5-only, so 0.0 domestic (household AC <5%);
# solid assumed domestic. whatif_heat is the domestic share of delivered
# heat overall (dominated by domestic gas) - estimate †.
DOM_SHARE = {
    "gas_space": 0.735, "gas_dhw": 0.859, "oil": 0.533, "elec_heat": 0.658,
    "bio_other": 0.571, "heat_networks": 0.516, "solid": 1.0, "cooling": 0.0,
    "whatif_heat": 0.73,
}


def _blend(p, fuel, comp):
    """Sector-blended unit price: the component's ECUK domestic share at
    the cap rate, the remainder at the QEP-anchored non-domestic rate.
    Only gas and electricity carry a distinct non-domestic price; other
    fuels fall through to their single estimate."""
    # The non-domestic rate is taken from the price dict, not from a module
    # constant, so it moves with the date like the domestic one. cap_prices
    # puts it there; the constant is only the fallback for a date outside the
    # series. Before 20 Nov 2026 this read a single flat pair for every week
    # in the record.
    nd = (p.get("_nd") or NONDOM_PRICES_P_PER_KWH).get(fuel)
    if nd is None:
        return p[fuel]
    s = DOM_SHARE[comp]
    return s * p[fuel] + (1.0 - s) * nd


def _pick(table, date_iso):
    """Last row of a (start, ...) table whose start is on or before the date,
    or None when the date precedes the table. Deliberately returns None
    rather than clamping: clamping is what silently priced a 2022 week at the
    April 2024 cap."""
    row = None
    for r in table:
        if r[0] <= date_iso:
            row = r
    return row


def cap_prices(date_iso):
    """Unit-price dict for date_iso, plus the period start (for provenance).

    One place resolves every price for a date: the domestic rate, the
    non-domestic rate and the flat non-cap fuels. Callers unpack two values
    as before; the basis and the non-domestic pair ride in the dict.

    DOMESTIC uses the quarterly cap where the table reaches, and the annual
    QEP-derived rows before that (DOM_PRE_CAP_ANNUAL), which are already
    scaled onto the cap basis so the series does not step at the join. The
    dict carries:
        _basis    'cap' | 'qep-annual' | 'fallback'
        _annual   True when the domestic rate is an annual mean, so a caller
                  can say so rather than implying quarterly resolution
        _nd       the non-domestic pair in force
    Returns the cap/period start as the second value; for a pre-cap date that
    is the YEAR start, which is the honest provenance for an annual figure."""
    p = dict(NONCAP_PRICES_P_PER_KWH)
    row = _pick(CAP_HISTORY, date_iso)
    if row is not None:
        p["gas"], p["elec"] = row[1], row[2]
        p["_basis"], p["_annual"] = "cap", False
        src = row[0]
    else:
        pre = _pick(DOM_PRE_CAP_ANNUAL, date_iso)
        if pre is not None:
            p["gas"], p["elec"] = pre[1], pre[2]
            p["_basis"], p["_annual"] = "qep-annual", True
            src = pre[0]
        else:
            # Older than either table. Price on the first cap row rather than
            # throw, but SAY so - this is the only path that still clamps.
            p["gas"], p["elec"] = CAP_HISTORY[0][1], CAP_HISTORY[0][2]
            p["_basis"], p["_annual"] = "fallback", False
            src = CAP_HISTORY[0][0]
    nd = _pick(NONDOM_PRICES_HISTORY, date_iso)
    p["_nd"] = ({"gas": nd[1], "elec": nd[2]} if nd
                else dict(NONDOM_PRICES_P_PER_KWH))
    p["_nd_from"] = nd[0] if nd else None
    return p, src


# --- shared estimator constants (single source for live panels + history) ---
# WINTER (space heating) seasonal efficiencies. In-situ, not nameplate.
EFF = {"gas": 0.835, "oil": 0.82, "bio": 0.70, "solid": 0.55,
       "heat_networks": 1.0, "resistive": 1.0}

# HOT WATER seasonal efficiencies, added 13 Aug 2026. A boiler serving only a
# cylinder fires in short bursts, cools between them, and returns water above
# the condensing dewpoint - so it loses its latent recovery outright. SAP has
# carried separate winter and summer seasonal efficiencies for exactly this
# reason since SAP 2009, and SAP is explicit that eta_water = eta_summer for
# ALL months: the summer figure is applied to hot water year-round, not
# seasonally.
#
# Derived as the mean SAP 2012 Table 4b summer/winter ratio applied to the
# existing winter anchor, so the sourced winter figures above are untouched:
#   gas  ratios 64/74, 74/84, 65/74, 75/84 -> mean 0.879 x 0.835 = 0.73
#   oil  ratios 68/80, 72/84, 73/82        -> mean 0.866 x 0.820 = 0.71
# Summer runs 82-89% of winter across the whole of Table 4b.
# Source: SAP 2012, Table 4b p207, files.bregroup.com/SAP/SAP-2012_9-92.pdf
#
# TWO CAVEATS. Table 4b is SAP's FALLBACK for boilers absent from the Product
# Characteristics Database, so it is conservative and skewed to older plant; a
# PCDB-weighted fleet figure would beat it. And Table 4c deducts 5 points from
# both figures where a regular boiler has no interlock - not applied here.
#
# GAS AND OIL ONLY. Table 4b does not cover bioenergy or solid fuel, so those
# keep their single figure rather than inheriting an invented ratio.
EFF_DHW = {"gas": 0.73, "oil": 0.71}

# UNDEMEANED COOLING MODEL, published as a comparison line 17 Aug 2026.
#
# The Tier 1 bars are the ECUK annual anchor shaped by cooling degree days.
# This is the SECOND, independent estimate: a whole-year regression of observed
# GB daily electricity demand on a constant, weekday/weekend, heating degree
# days, a convex cooling limb, embedded solar, holiday and shutdown terms and a
# linear trend, fitted on UNDEMEANED data so the level survives. Because it
# models the baseline rather than removing it, it recovers a level that the
# within-class centred estimator cannot (see the methodology, 4.5 and 4.6).
#
# Only the COOLING LIMB is carried here. On the full 800-day record with solar
# controlled it is 0.6109 GWh of electricity per (degC above 11.0) squared,
# giving 4.56 TWh/yr against the ECUK cooling anchor of 4.78 - 95%.
#
# PINNED to the fit that produced it, because a coefficient without its vintage
# is not reproducible: re-derive with analysis/fit_cooling.py and update all
# four fields together. The base specification without the calendar terms gives
# a balance point of 12.0 and 3.59 TWh, 75% of the anchor; the spread between
# 75% and 95% is the honest uncertainty on this line.
#
# RE-DERIVED 22 Oct 2026, and the reason matters more than the new numbers.
#
# Two faults were found in the 17 Aug pin. First, TC_SCAN started at 11.0 and
# every fit returned exactly 11.0: the balance point was sitting on the floor
# of its own search grid. Unconstrained it is 9.5. Second, and worse, the
# optimum is SHALLOW. k and the balance point trade off almost exactly, so a
# wide range of pairs fits the demand record equally well - while the annual
# integral, which is what this file publishes, moves by a factor of three
# across that range. A point estimate off the scan was never a measurement.
#
# So this now carries an INTERVAL. The honest statement is that an independent
# regression puts weather-driven cooling electricity between 3.9 and 6.3 TWh/yr
# and the ECUK anchor of 4.78 sits inside that - consistent with the anchor,
# not a confirmation of it. An anchor anywhere in that span would also have sat
# inside. The old "95%" claimed a precision the method does not have.
#
# The width is a sample-length limit: two complete summers is very little
# cooling signal. docs/daily.json (append-only, never trimmed) is the record
# the fits are rebuilt from; since 20 Nov 2026 no store trims, so the record
# simply grows. Five summers should tighten this materially - though note the
# ridge finding of 19 Nov: 50% more signal narrowed the interval 6% where
# sampling predicted ~18%, so more summers help less than the width suggests.
#
# PINNED WITH ITS WINDOW. The 17 Aug fit could not be reproduced at all,
# because retro.json had since rolled and the days it used were gone. Any pin
# here must name the window so it can be rebuilt from the daily archive:
#   python3 analysis/fit_cooling.py docs/daily.json
#
# RE-PINNED 22 Oct 2026 (second time that day) on the backfilled archive:
# 1,010 days and THREE complete summers instead of two, after
# scripts/backfill_daily.py reached back to 2024-01-15. NESO's year resources
# stop there - a 2023 fetch returns nothing - so three summers is the ceiling
# this feed allows.
#
# THE RESULT FALSIFIED THE REASON GIVEN FOR THE WIDTH. On two summers the
# interval was 81-131% of the anchor and the stated explanation, published on
# the panel, was that the width was a sample-length limit that would narrow as
# the record grew. A 50% increase in cooling signal moved it to 77-124%:
# 50 points wide to 47. Sampling noise alone should have narrowed it by about
# 18% (1/sqrt(n) on summers), to roughly 41 points. It narrowed by 6%.
#
# So the non-identification is STRUCTURAL, not statistical. k and the balance
# point trade off because, across the temperatures Britain actually sees, a
# gentle slope from a low threshold and a steep one from a high threshold
# trace nearly the same curve. More summers of the same range measure that
# ridge more precisely; they do not break it. Breaking it needs a term that
# changes the SHAPE of the response rather than rescaling it - latent load
# (see analysis/fetch_humidity.py) is the leading candidate and is the next
# test, not a further extension of the record.
# REFITTED 20 Nov 2026 on five complete summers. The previous pin - 4.56 TWh,
# 95% of the anchor, 2024-01-15 to 2026-10-20 - was fitted on the deepest
# record then available, 1,010 days and three summers. The archive backfill
# reached 2021-11-18 the same day and the fit moved materially:
#
#     window                    days   cooling TWh          % of anchor
#     2024-01-15 - 2026-10-20   1010   4.56 (3.68-5.92)     95 (77-124)
#     2023-11-18 - 2026-11-18   1097   3.76 (3.13-4.74)     79 (66- 99)
#     2022-11-18 - 2026-11-18   1462   3.59 (2.97-4.38)     75 (62- 92)
#     2021-11-18 - 2026-11-18   1827   3.52 (3.01-4.19)     74 (63- 88)   <-
#
# Two things follow, and both are stated on the site rather than smoothed over.
# The estimate CONVERGES as summers are added, so the five-summer fit is taken
# as the best identified. And the old central value is OUTSIDE the new interval
# while the new one sits just under the old - so the narrowing excluded it.
#
# The superseded figure was not a mistake in the arithmetic: re-cut to its own
# window on the backfilled archive it still reproduces to the digit, and the
# 1,039 overlapping days are byte-identical. It was a window effect. Its window
# simply held fewer hot days than the record now does.
#
# WHAT THIS MEANS FOR THE COMPARISON. At 63-88% the interval now EXCLUDES the
# ECUK anchor, where the old one contained it. The two estimators no longer
# corroborate each other; they disagree, and which way it resolves is open.
UNDEMEANED_FIT = {
    "k_elec_GWh_per_degC2": 0.5784,   # cooling limb coefficient
    "balance_point_C": 10.75,
    "fitted_on": "2026-11-20",
    "window_start": "2021-11-18",     # the fit is reproducible only with these
    "window_end": "2026-11-18",
    "record": "1827 days, 5 complete summers, 9 regressors",
    # Days at or above 25 C in the pinned window, counted from the archive's
    # temp_C on 20 Nov 2026: 2 in 2022, 5 in 2026, none in 2023-2025. Pinned
    # rather than written into the prose because the fold states it, and a
    # number the page states about a record it does not read is a number that
    # will drift - this one grows with the archive while the window does not.
    "hot_days_ge_25C": 7,
    "annual_TWh": 3.52,
    "annual_TWh_lo": 3.01,            # 95% profile interval on the balance
    "annual_TWh_hi": 4.19,            # point, which dominates the uncertainty
    "balance_point_lo_C": 9.50,
    "balance_point_hi_C": 11.75,
    "share_of_ecuk_anchor_pct": 74,
    "share_lo_pct": 63,
    "share_hi_pct": 88,
}


def check_undemeaned_pin(archive_dates, fit=UNDEMEANED_FIT):
    """Warn when the published cooling coefficient has outlived its window.

    The pin is a constant; the data underneath it moves. Between 17 Aug and
    22 Oct 2026 the rolling window advanced two months and the same code gave
    84% of the anchor instead of the pinned 95%, with nothing in the repo
    noticing. This is the thing that should have fired then.

    Returns a list of complaints, empty when the pin is current.
    """
    out = []
    if not archive_dates:
        return out
    have_end = archive_dates[-1]
    want_end = fit.get("window_end")
    if not want_end:
        return ["UNDEMEANED_FIT has no window_end - it cannot be reproduced "
                "or checked. Re-derive and pin the window."]
    gap = ((dt.date.fromisoformat(have_end)
            - dt.date.fromisoformat(want_end)).days)
    if gap >= 90:
        out.append(
            "UNDEMEANED_FIT is pinned to a window ending %s but the archive "
            "now reaches %s (%d days on). Re-derive with "
            "'python3 analysis/fit_cooling.py docs/daily.json' and update the "
            "whole block, interval included." % (want_end, have_end, gap))
    if archive_dates[0] > fit.get("window_start", "9999"):
        out.append(
            "UNDEMEANED_FIT's window starts %s but the archive only reaches "
            "back to %s, so the pinned fit CANNOT be reproduced. Backfill "
            "(scripts/backfill_daily.py) or re-derive on what exists."
            % (fit.get("window_start"), archive_dates[0]))
    return out

UK_POP_M = 68.0   # population convention per the July 2026 cross-calibration †
HP_ELEC_TWH = 2.0   # hydronic-domestic subset consistent with the ECUK
                    # elec-space anchors. NOT the DUKES 2026 ambient-heat
                    # figure (1,386 ktoe = 16.1 TWh, 2025), which counts
                    # reversible air-con heating across the non-domestic
                    # fleet - different basis. Reconcile at the ECUK 2026
                    # re-anchor, not before.
HP_SPF = 2.8
# ECUK U5 "cooling and ventilation" is a MERGED line. BEES (DECC/BEIS, the
# survey ECUK's services end-use split is built from) reports its components
# separately: fans 6,030 GWh of electricity against space cooling 5,090 plus
# humidification 130. That is 54% ventilation to 46% cooling, and it replaces
# the undocumented 50/50 this file carried until 11 Aug 2026.
# Cross-check: US CBECS 2018 separates the two and gives ventilation 18% of
# commercial electricity against cooling 14% - a 56:44 ratio, from a much
# hotter climate, which is close enough to be reassuring about the shape.
VENT_SHARE = 0.54
COOL_SHARE = 0.46

# Ventilation has NO COP. Fans move air, not heat - there is no thermodynamic
# multiplication to have, and fan work dissipates as heat INSIDE the space, so
# if anything it is a cooling load rather than a cooling output. It is
# therefore reported as its own service and kept OUT of cooling_delivered,
# which from 11 Aug 2026 means cooling service only.
COOL_EER = 3.0
# The fleet-average seasonal ratio of cooling delivered to electricity drawn,
# across every system type, vintage and control regime in the UK non-domestic
# stock. NOT a machine rating. Bounded 2.5-4.0: legacy oversized plant runs
# nearer 2, modern commissioned VRF nearer 4.5, and BEES excludes fan energy
# from its space-cooling line so the boundary here is chiller-plus-condenser
# rather than whole-system. Published as a range on the panel; the largest
# single unverified assumption left in the cooling chain.
COOL_EER_RANGE = (2.5, 4.0)
HP_FLAT_SHARE = 0.15   # HP hot-water runs year-round (assumption)

GSHP_SPF = 3.24   # Energy Systems Catapult in-situ GSHP average
ASHP_SPF = 2.80   # ESC Electrification of Heat median
PASSIVE_COOL_COP = 20.0  # illustrative mid-range of 15-30
# NETWORKED GEOTHERMAL SEASONAL PERFORMANCE FACTOR.
#
# SYSTEM BOUNDARY, stated explicitly 17 Aug 2026 after peer review. This is a
# WHOLE-SYSTEM figure on the same basis as the SEPEMO H4 boundary the
# air-source and ground-source anchors use, so the comparison is like-for-like
# rather than a monitored H4 SPF against an optimistic plant-only COP.
#
# INSIDE the boundary - all electricity metered at the network supply point:
#   building heat pumps and their auxiliaries
#   district ambient-loop circulation
#   source-side and primary-loop circulation
#   production and injection well pumping, where a doublet is used
#   controls and BMS
#   seasonal store charging and recovery losses, as a round-trip penalty
# The denominator is heat DELIVERED AT THE BUILDING BOUNDARY, so distribution
# heat losses are inside the boundary too.
#
# OUTSIDE the boundary:
#   groundwater treatment, where required - not modelled, would reduce SPF
#   customer-side emitters and internal distribution
#   embodied energy and construction
#
# 5.0 IS AN ESTIMATE, NOT A MEASUREMENT, and it is the single assumption most
# of the site's strongest conclusions rest on. No monitored UK ambient-loop
# network fleet exists to anchor it. It is internally consistent with the
# 19.6 C source temperature at the same Carnot fraction as the two monitored
# routes (see retro.py), but that consistency is how the source was derived
# and is not independent evidence for either - see the methodology, 10.3.
#
# The parasitic load is the part most likely to be optimistic: European
# ambient-loop and ATES schemes commonly report 5-15% of delivered heat going
# to circulation, and at the upper end of that range a plant-level 5.6 becomes
# a whole-system 5.0. That is the arithmetic this figure assumes.
GEO_NETWORK_SCOP = 5.0   # whole-system, H4-equivalent boundary - estimate

# Indigenous (UK-origin) shares of purchased energy - flagged estimates:
#  gas ~42% UK (DUKES 2026 supply pool); oil ~30%; bio ~80% (domestic
#  wood); solid ~20%; heat networks ~40% (gas-driven); electricity ~75%
#  (net imports ~10% + imported-gas share of CCGT). Ambient/ground heat
#  is 100% indigenous but is not purchased energy.
# Indigenous input-origin shares. gas: DUKES 2026 (2025 data) supply
# pool - UKCS production 332.1 + UK biomethane 5.8 over gross supply
# 810.2 TWh (prod + imports 463.7 + biomethane + stock draw 8.6) =
# 41.7% -> 0.42, proportional-draw basis (DUKES's own framing: Norway
# = 40% of gross supply). Was 0.38 (stale vintage). oil/bio/solid/
# networks/elec are estimates †. UPDATE with each DUKES (late July).
INDIG = {"gas": 0.42, "oil": 0.30, "bio": 0.80, "solid": 0.20,
         "heat_networks": 0.40, "elec": 0.75}

R_SHIFT = 0.20   # the 20% geothermal what-if

# Combustion factors: DESNZ GHG conversion factors 2025, gross CV,
# gCO2e/kWh fuel: natural gas ~183, kerosene ~247, coal ~345.
# Bioenergy combustion counted at 0 (biogenic convention) - supply-chain
# emissions excluded and noted. Heat networks assumed gas-fired (†).
CF = {"gas": 183.0, "oil": 247.0, "solid": 345.0, "bio": 0.0,
      "heat_networks": 183.0}


def _cost_m(gwh, price):
    return gwh * price / 100.0  # GWh * p/kWh -> £m


def _indig_pct(gas_gwh, oil_gwh, bio_gwh, solid_gwh, hn_gwh, elec_gwh,
               total_gwh):
    if not total_gwh:
        return None
    e = (gas_gwh * INDIG["gas"] + oil_gwh * INDIG["oil"]
         + bio_gwh * INDIG["bio"] + solid_gwh * INDIG["solid"]
         + hn_gwh * INDIG["heat_networks"] + elec_gwh * INDIG["elec"])
    return round(100.0 * e / total_gwh, 0)


def compute_week(gas_space_wk, hdd_wk, cdd_wk, hdd_12m, cdd_12m, p):
    """All weekly derived quantities for any 7-day window: mix, useful,
    bills, what-ifs, indigenous shares. THE single source of estimators -
    the live panels and every ticker history entry go through here, so
    live and backfilled weeks are comparable by construction (spec: 'same
    estimators'). Emissions need a grid CI and live in
    compute_week_emissions()."""
    f_flat = 7.0 / 365.0
    f_h = (hdd_wk / hdd_12m) if hdd_12m else 0.0
    f_c = (cdd_wk / cdd_12m) if cdd_12m else 0.0
    # Per-fuel GB shares, not one gas-network factor for everything.
    A = {k: v * gb_share(k) * 1000.0 for k, v in ANNUAL_TWH.items()}  # GWh, GB

    # Oil is one bar on the page but TWO loads on two different curves, and
    # from 13 Aug 2026 two different efficiencies. Kept separate through the
    # efficiency step and only summed for display - summing first would apply
    # a space-heating efficiency to hot water.
    oil_space_wk = A["oil_space"] * f_h
    oil_dhw_wk = A["oil_dhw"] * f_flat

    # ONE SPLIT, BOTH CHAINS. The purchased and useful sides previously
    # disagreed: this dict used a 50/50 flat/weather split while the useful
    # conversion below used the sourced BEES 54:46 ventilation:cooling split.
    # Same annual, two answers. Found in peer review, 17 Aug 2026 - the 54:46
    # correction had propagated to the useful side only.
    #
    # Ventilation runs whenever buildings are occupied and does not respond to
    # temperature, so it takes the FLAT shape; cooling is weather-driven and
    # takes the COOLING shape. The shares are the ones sourced at VENT_SHARE
    # and COOL_SHARE, not a half-and-half approximation of them.
    vent_elec = A["cooling_vent"] * VENT_SHARE * f_flat
    cool_elec = A["cooling_vent"] * COOL_SHARE * f_c

    mix = {
        "gas_space": round(gas_space_wk, 0),                # live estimate
        "gas_dhw": round(A["gas_dhw"] * f_flat, 0),
        "oil": round(oil_space_wk + oil_dhw_wk, 0),
        "elec_heat": round(A["elec_space"] * f_h + A["elec_dhw"] * f_flat, 0),
        "bio_other": round(A["bio_space"] * f_h + A["bio_dhw"] * f_flat, 0),
        "heat_networks": round(A["heat_networks"] * f_h, 0),
        "solid": round(A["solid"] * f_h, 0),
        # COOLING ONLY. Ventilation is a separate component below.
        #
        # These were one line until 17 Aug 2026, and combining them was not
        # merely a labelling problem. Fans move air, not heat: there is no
        # coefficient of performance to apply, and a geothermal network cannot
        # displace them. But the 20% what-if was reducing mix["cooling"] by a
        # fifth and replacing it with near-passive ground cooling - which,
        # with ventilation inside that figure, claimed a fifth of Britain's
        # fan energy could be served by a borehole. It cannot. Splitting the
        # two removes that from the saving.
        "cooling": round(cool_elec, 0),
        "ventilation": round(vent_elec, 0),
    }
    combustion = (mix["gas_space"] + mix["gas_dhw"] + mix["oil"]
                  + mix["bio_other"] + mix["solid"])
    total = sum(mix.values())

    # useful heat & cool delivered (dual-bar basis). Conversion factors,
    # sourced/flagged: gas boiler in-situ 0.835 (RAP/field trials 82.5-85%);
    # oil 0.82 (est., older stock); bio 0.70 (est., stoves/boilers range
    # 60-80%); solid 0.55 (est.); heat networks 1.0 (ECUK 'heat' is
    # delivered heat; upstream losses excluded); resistive electric 1.0.
    # Heat pumps: domestic HP electricity 2.0 TWh/yr (ECUK 2025, 169 ktoe,
    # 2024; non-domestic HP excluded - understates). Blended SPF 2.8
    # (Energy Systems Catapult EoH median ASHP 2.80; GSHP 3.24).
    # Cooling: EER 3.0 (assumption) on the CDD-shaped half; ventilation
    # counted at 1.0 (fan energy delivers a service, not multiplied).
    hp_elec_wk = HP_ELEC_TWH * GB_SHARE["default"] * 1000.0 * (
        (1 - HP_FLAT_SHARE) * f_h + HP_FLAT_SHARE * f_flat)
    hp_elec_wk = min(hp_elec_wk, mix["elec_heat"])       # cannot exceed segment
    resistive_wk = mix["elec_heat"] - hp_elec_wk
    hp_heat_wk = hp_elec_wk * HP_SPF
    hp_ambient_wk = hp_heat_wk - hp_elec_wk

    # vent_elec and cool_elec are computed once, above the mix, and reused
    # here - so the purchased and useful sides cannot drift apart again.
    vent_useful = vent_elec * 1.0        # air movement, no COP - see above
    cool_useful = cool_elec * COOL_EER   # cooling service only

    useful = {
        "gas_space": round(mix["gas_space"] * EFF["gas"], 0),
        # Hot water at the SUMMER efficiency, year-round - see EFF_DHW.
        "gas_dhw": round(mix["gas_dhw"] * EFF_DHW["gas"], 0),
        "oil": round(oil_space_wk * EFF["oil"]
                     + oil_dhw_wk * EFF_DHW["oil"], 0),
        "bio_other": round(mix["bio_other"] * EFF["bio"], 0),
        "solid": round(mix["solid"] * EFF["solid"], 0),
        "heat_networks": round(mix["heat_networks"] * EFF["heat_networks"], 0),
        "elec_resistive": round(resistive_wk, 0),
        "hp_electricity": round(hp_elec_wk, 0),
        "hp_ambient": round(hp_ambient_wk, 0),
        "cooling_delivered": round(cool_useful, 0),
        "ventilation": round(vent_useful, 0),
    }
    # Hot water wastes MORE than space heating, not the same - a cylinder
    # boiler loses its latent recovery. Split by end use here too, or the
    # waste bar contradicts the useful bar it is derived from.
    wasted = round(
        mix["gas_space"] * (1 - EFF["gas"])
        + mix["gas_dhw"] * (1 - EFF_DHW["gas"])
        + oil_space_wk * (1 - EFF["oil"])
        + oil_dhw_wk * (1 - EFF_DHW["oil"])
        + mix["bio_other"] * (1 - EFF["bio"])
        + mix["solid"] * (1 - EFF["solid"]), 0)

    # national weekly bill: energy-in mix x sector-blended unit prices -
    # each component's ECUK domestic share at the Ofgem cap, the services
    # remainder at QEP-anchored non-domestic rates †. GWh x p/kWh = £10k.
    bill = {
        "gas": round(_cost_m(mix["gas_space"], _blend(p, "gas", "gas_space"))
                     + _cost_m(mix["gas_dhw"], _blend(p, "gas", "gas_dhw")), 0),
        "oil": round(_cost_m(mix["oil"], p["oil"]), 0),
        "electric_heat": round(_cost_m(mix["elec_heat"],
                                       _blend(p, "elec", "elec_heat")), 0),
        "bio_other": round(_cost_m(mix["bio_other"], p["bio"]), 0),
        "heat_networks": round(_cost_m(mix["heat_networks"],
                                       p["heat_networks"]), 0),
        "solid": round(_cost_m(mix["solid"], p["solid"]), 0),
        "ventilation": round(_cost_m(mix["ventilation"],
                                     _blend(p, "elec", "cooling")), 1),
        "cooling": round(_cost_m(mix["cooling"],
                                 _blend(p, "elec", "cooling")), 0),
    }
    bill_heat = round(bill["gas"] + bill["oil"] + bill["electric_heat"]
                      + bill["bio_other"] + bill["heat_networks"]
                      + bill["solid"], 0)
    bill_cool = bill["cooling"]

    # what-if: same useful heat & cool delivered via geothermal networks
    useful_heat_wk = (useful["gas_space"] + useful["gas_dhw"] + useful["oil"]
                      + useful["bio_other"] + useful["solid"]
                      + useful["heat_networks"] + useful["elec_resistive"]
                      + useful["hp_electricity"] + useful["hp_ambient"])
    whatif_heat_m = _cost_m(useful_heat_wk / GEO_NETWORK_SCOP,
                            _blend(p, "elec", "whatif_heat"))
    whatif_cool_m = _cost_m(useful["cooling_delivered"] / PASSIVE_COOL_COP,
                            _blend(p, "elec", "cooling"))

    total_in = sum(mix.values())
    # purchased basis (retained for methods note / continuity)
    indig_now = _indig_pct(mix["gas_space"] + mix["gas_dhw"], mix["oil"],
                           mix["bio_other"], mix["solid"],
                           mix["heat_networks"],
                           mix["elec_heat"] + mix["cooling"] + mix["ventilation"], total_in)

    # services basis: indigenous share of useful heat & cool DELIVERED.
    # Each service inherits the indigenous share of its energy input;
    # harvested ambient/ground heat counts at 100% (Eurostat/DUKES treat
    # it as renewable supply). Cooling's delivered multiple is leverage,
    # not input - it inherits its electricity's share.
    e_now = (useful["gas_space"] * INDIG["gas"]
             + useful["gas_dhw"] * INDIG["gas"]
             + useful["oil"] * INDIG["oil"]
             + useful["bio_other"] * INDIG["bio"]
             + useful["solid"] * INDIG["solid"]
             + useful["heat_networks"] * INDIG["heat_networks"]
             + useful["elec_resistive"] * INDIG["elec"]
             + useful["hp_electricity"] * INDIG["elec"]
             + useful["hp_ambient"] * 1.0
             + useful["cooling_delivered"] * INDIG["elec"]
             + useful["ventilation"] * INDIG["elec"])
    useful_total = (useful["gas_space"] + useful["gas_dhw"] + useful["oil"]
                    + useful["bio_other"] + useful["solid"]
                    + useful["heat_networks"] + useful["elec_resistive"]
                    + useful["hp_electricity"] + useful["hp_ambient"]
                    + useful["cooling_delivered"] + useful["ventilation"])
    indig_services_now = (round(100.0 * e_now / useful_total, 1)
                          if useful_total else None)

    # what-if: 20% of heat & cooling service moved to geothermal networks
    R = R_SHIFT
    heat_repl_elec = (useful_heat_wk * R) / GEO_NETWORK_SCOP
    cool_repl_elec = (useful["cooling_delivered"] * R) / PASSIVE_COOL_COP
    adj = {
        "gas": (mix["gas_space"] + mix["gas_dhw"]) * (1 - R),
        "oil": mix["oil"] * (1 - R),
        "bio": mix["bio_other"] * (1 - R),
        "solid": mix["solid"] * (1 - R),
        "hn": mix["heat_networks"] * (1 - R),
        # ventilation is NOT reduced: a geothermal network does not displace
        # fans, so the full fan load carries through the what-if unchanged.
        "elec": (mix["elec_heat"] * (1 - R) + mix["cooling"] * (1 - R)
                 + mix["ventilation"]
                 + heat_repl_elec + cool_repl_elec),
    }
    new_total = sum(adj.values())
    # PUBLISHED so the saving can be seen to be NET. The headline is a
    # difference of two bills, and nothing in that difference shows what the
    # geothermal route itself buys - which invites the reasonable question of
    # whether the saving is gross. It is not: heat_repl_elec and
    # cool_repl_elec are inside adj["elec"] above, and therefore inside
    # bill_20. On the current figures the route consumes about a fifth of the
    # heat it delivers, roughly GBP 4bn a year, so a gross figure would be
    # more than double the published saving.
    wf_route_elec_wk = round(heat_repl_elec + cool_repl_elec, 0)
    # what-if purchased split: cooling = kept cooling electricity + its
    # near-passive replacement; heat = everything else
    wf_cooling = mix["cooling"] * (1 - R) + cool_repl_elec
    wf_heat = new_total - wf_cooling
    indig_20 = _indig_pct(adj["gas"], adj["oil"], adj["bio"], adj["solid"],
                          adj["hn"], adj["elec"], new_total)

    # services-basis what-if: the shifted fifth of heat is delivered as
    # (1/SCOP) grid electricity + (1-1/SCOP) harvested ground heat; the
    # shifted fifth of cooling as near-passive (1/COP elec + leverage
    # treated as ground-enabled, indigenous)
    heat_services = (useful["gas_space"] + useful["gas_dhw"] + useful["oil"]
                     + useful["bio_other"] + useful["solid"]
                     + useful["heat_networks"] + useful["elec_resistive"]
                     + useful["hp_electricity"] + useful["hp_ambient"])
    # Cooling only. Ventilation is a delivered service and is in useful_total
    # and the indigenous share, but geothermal does not replace fans and fan
    # energy is not cooling - so it is absent from the what-if by design.
    cool_services = useful["cooling_delivered"]
    kept = {k: useful[k] * (1 - R) for k in
            ("gas_space", "gas_dhw", "oil", "bio_other", "solid",
             "heat_networks", "elec_resistive", "hp_electricity",
             "hp_ambient", "cooling_delivered")}
    e_kept = (kept["gas_space"] * INDIG["gas"] + kept["gas_dhw"] * INDIG["gas"]
              + kept["oil"] * INDIG["oil"] + kept["bio_other"] * INDIG["bio"]
              + kept["solid"] * INDIG["solid"]
              + kept["heat_networks"] * INDIG["heat_networks"]
              + kept["elec_resistive"] * INDIG["elec"]
              + kept["hp_electricity"] * INDIG["elec"]
              + kept["hp_ambient"] * 1.0
              + kept["cooling_delivered"] * INDIG["elec"])
    shifted_heat = heat_services * R
    shifted_cool = cool_services * R
    e_shift = (shifted_heat * ((1 / GEO_NETWORK_SCOP) * INDIG["elec"]
                               + (1 - 1 / GEO_NETWORK_SCOP) * 1.0)
               + shifted_cool * ((1 / PASSIVE_COOL_COP) * INDIG["elec"]
                                 + (1 - 1 / PASSIVE_COOL_COP) * 1.0))
    tot_services = heat_services + cool_services
    indig_services_20 = round(100.0 * (e_kept + e_shift) / tot_services, 1) \
        if tot_services else None
    gas_tot = mix["gas_space"] + mix["gas_dhw"]
    pg_eff = ((mix["gas_space"] * _blend(p, "gas", "gas_space")
               + mix["gas_dhw"] * _blend(p, "gas", "gas_dhw")) / gas_tot
              if gas_tot else p["gas"])
    elec_parts = [(mix["elec_heat"] * (1 - R), _blend(p, "elec", "elec_heat")),
                  (mix["cooling"] * (1 - R), _blend(p, "elec", "cooling")),
                  (heat_repl_elec, _blend(p, "elec", "whatif_heat")),
                  (cool_repl_elec, _blend(p, "elec", "cooling"))]
    elec_vol = sum(v for v, _ in elec_parts)
    pe_eff = (sum(v * pr for v, pr in elec_parts) / elec_vol
              if elec_vol else p["elec"])
    bill_20 = (_cost_m(adj["gas"], pg_eff) + _cost_m(adj["oil"], p["oil"])
               + _cost_m(adj["bio"], p["bio"])
               + _cost_m(adj["solid"], p["solid"])
               + _cost_m(adj["hn"], p["heat_networks"])
               + _cost_m(adj["elec"], pe_eff))
    # what-if bill split: cooling = kept cooling electricity + its
    # near-passive replacement, priced at the cooling blend; heat = rest
    wf_bill_cool = _cost_m(mix["cooling"] * (1 - R) + cool_repl_elec,
                           _blend(p, "elec", "cooling"))
    wf_bill_heat = bill_20 - wf_bill_cool

    return {
        "f_flat": f_flat, "f_h": f_h, "f_c": f_c,
        "mix": mix, "combustion": combustion, "total": total,
        # the two halves of mix["cooling"], so the headline can name them
        "vent_elec_wk": round(vent_elec, 0),
        "cool_elec_wk": round(cool_elec, 0),
        "useful": useful, "wasted": wasted,
        "bill": bill, "bill_heat": bill_heat, "bill_cool": bill_cool,
        "useful_heat_wk": useful_heat_wk,
        "whatif_heat_m": whatif_heat_m, "whatif_cool_m": whatif_cool_m,
        "wf_route_elec_wk": wf_route_elec_wk,
        "total_in": total_in,
        "indig_now": indig_now, "indig_services_now": indig_services_now,
        "heat_repl_elec": heat_repl_elec, "cool_repl_elec": cool_repl_elec,
        "wf_heat": wf_heat, "wf_cooling": wf_cooling,
        "wf_bill_heat": wf_bill_heat, "wf_bill_cool": wf_bill_cool,
        "adj": adj, "new_total": new_total,
        "indig_20": indig_20, "indig_services_20": indig_services_20,
        "bill_20": bill_20,
    }


def compute_week_emissions(r, grid_ci):
    """Weekly emissions for a compute_week() result at a given grid CI
    (g/kWh). Live weeks use the trailing-7-day NESO mean; history weeks use
    that week's historical mean from the same API."""
    mix = r["mix"]
    em = {
        "gas": (mix["gas_space"] + mix["gas_dhw"]) * CF["gas"],
        "oil": mix["oil"] * CF["oil"],
        "bio_other": mix["bio_other"] * CF["bio"],
        "solid": mix["solid"] * CF["solid"],
        "heat_networks": mix["heat_networks"] * CF["heat_networks"],
        "elec_heat": mix["elec_heat"] * grid_ci,
        "cooling": mix["cooling"] * grid_ci,
        "ventilation": mix["ventilation"] * grid_ci,
    }
    # ventilation is neither heat nor cooling: it is fan energy. Excluded
    # from the heat figure, reported on its own, and NOT removed by the
    # what-if below - a geothermal network does not displace fans.
    em_heat = sum(v for k, v in em.items()
                  if k not in ("cooling", "ventilation"))
    em_total = sum(em.values())
    # what-if: same 20% shift as the cost what-if
    em_removed = R_SHIFT * (em["gas"] + em["oil"] + em["bio_other"]
                            + em["solid"] + em["heat_networks"]
                            + em["elec_heat"] + em["cooling"])
    em_added = (r["heat_repl_elec"] + r["cool_repl_elec"]) * grid_ci
    return {
        "em": em,
        "week_kt": round(em_total / 1000.0, 0),
        "week_heat_kt": round(em_heat / 1000.0, 0),
        "week_cool_kt": round(em["cooling"] / 1000.0, 0),
        "whatif_kt": round((em_total - em_removed + em_added) / 1000.0, 0),
        "whatif_cool_kt": round((r["mix"]["cooling"] * (1 - R_SHIFT)
                                 + r["cool_repl_elec"]) * grid_ci
                                / 1000.0, 0),
        "whatif_heat_kt": round((em_total - em_removed + em_added
                                 - (r["mix"]["cooling"] * (1 - R_SHIFT)
                                    + r["cool_repl_elec"]) * grid_ci)
                                / 1000.0, 0),
        "saving_kt": round((em_removed - em_added) / 1000.0, 0),
    }


# --- historical weekly grid CI (for ticker backfill) -------------------------
CI_RANGE_URL = ("https://api.carbonintensity.org.uk/intensity/"
                "{}T00:00Z/{}T23:59Z")


def fetch_ci_weekly_mean(start_iso, end_iso):
    """Mean GB grid intensity (gCO2/kWh) over an inclusive date range
    (<=14 days per the API), from half-hourly actuals (forecast fallback).
    NESO CI API archives to 2018, so every gas-live week has a live CI."""
    req = urllib.request.Request(CI_RANGE_URL.format(start_iso, end_iso),
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    vals = []
    for item in data.get("data", []):
        v = (item.get("intensity") or {}).get("actual")
        if v is None:
            v = (item.get("intensity") or {}).get("forecast")
        if v is not None:
            vals.append(v)
    if len(vals) < 48 * 5:   # want most of the week's half-hours
        raise ValueError(f"thin CI data {start_iso}..{end_iso}: {len(vals)}")
    return round(sum(vals) / len(vals), 1)


def ols(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    ss_tot = sum((yi - my) ** 2 for yi in y)
    ss_res = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    resid_se = (ss_res / (n - 2)) ** 0.5 if n > 2 else 0.0
    return slope, intercept, r2, resid_se


def load_previous():
    try:
        with open(OUT_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


ANCHOR_YEAR = 2024   # ECUK vintage the space-heat anchor is drawn from;
#                      moves to 2025 at the ECUK 2026 re-anchor. The
#                      weather normalisation divides by this year's HDD,
#                      so the degree-day fetch must ALWAYS cover it in
#                      full - a rolling window silently clips January and
#                      the ratio drifts (found 8 Aug 2026: hdd_2024 fell
#                      2137 -> 2048 in six days, ratio 1.092 -> 1.047,
#                      purely because the window had rolled past 11 Jan).
HISTORY_SCHEMA = 9   # 2: indig_pct at one decimal (restated Jul 2026)
                     # 3: per-week heat/cool split of purchased energy
                     #    (restated Aug 2026, same stored-CI mechanism)
                     # 4: what-if heat/cool split per week (Aug 2026)
                     # 5: bill and emissions heat/cool splits (Aug 2026)
                     # 6: what-if bill and emissions splits (Aug 2026)
                     # 7: per-fuel in/useful block on every entry, so the
                     #    energy bars can be re-totalled over a trend
                     #    window (Irish sibling's fix, ported 7 Aug 2026)
                     # 8: ventilation as its own per-week field, so the
                     #    trend layer can report it apart from cooling
                     #    (17 Aug 2026)
                     # 9: wf_fuels - the what-if twin of the per-fuel block,
                     #    with the ground heat and the avoided boiler losses
                     #    beside it, so the what-if energy bars honour the
                     #    window buttons instead of silently showing the
                     #    live week under an annual heading (21 Nov 2026)

# ANCHOR_EPOCH tracks a changed BASIS rather than new FIELDS: bump it
# whenever a constant changes what a past week WOULD have computed, and
# every recomputable stored week is restated. Schema adds fields; epoch
# rewrites values. (Irish handover, trap 2.)
# Set True for ONE run to force a full restatement regardless of the stored
# epoch. Needed when a previous run advanced the epoch without completing the
# restatement - which was possible until 19 Aug 2026, when the markers were
# reordered to write only on success. Return to False afterwards.
FORCE_RESTATE = False

ANCHOR_EPOCH = 2     # 1: introduced 8 Aug 2026 with the anchor-year fix.
                     # 2: 17 Aug 2026. Ventilation removed from "cooling"
                     #    throughout. Every stored week computed before this
                     #    carries cooling AND ventilation in its cooling
                     #    figure - roughly double the corrected value - and
                     #    a what-if that displaced a fifth of national fan
                     #    energy with ground cooling. A pure BASIS change:
                     #    the same week now computes a different number, so
                     #    epoch rather than schema. Restates every week still
                     #    inside the live gas window.
                     #    No basis change has been made since, so this
                     #    has not yet been bumped - the next entry goes
                     #    here, NOT on the schema list above.


def _fuel_block(r):
    """Compact per-fuel {in, useful} for one week, so a trend window can
    re-total the energy bars. Short keys: this rides in every entry.
    Cooling carried separately - its useful output legitimately exceeds
    its input, so it must be excluded from any conversion-loss sum."""
    mix, use = r["mix"], r["useful"]
    keys = ("gas_space", "gas_dhw", "oil", "bio_other", "solid",
            "heat_networks", "elec_heat")
    out = {}
    for k in keys:
        i_ = round(mix.get(k, 0.0), 1)
        if k == "elec_heat":
            u_ = round(use.get("elec_resistive", 0.0)
                       + use.get("hp_electricity", 0.0)
                       + use.get("hp_ambient", 0.0), 1)
        else:
            u_ = round(use.get(k, 0.0), 1)
        if i_ or u_:
            out[k] = {"i": i_, "u": u_}
    # split the electric leg so the useful bar can rebuild its segments
    for k in ("elec_resistive", "hp_electricity", "hp_ambient"):
        v = round(use.get(k, 0.0), 1)
        if v:
            out.setdefault("_u", {})[k] = v
    out["cool"] = {"i": round(mix.get("cooling", 0.0), 1),
                   "u": round(use.get("cooling_delivered", 0.0), 1)}
    return out


def _wf_fuel_block(r):
    """The WHAT-IF twin of _fuel_block, added 21 Nov 2026.

    Without it the what-if energy bars could only ever show the live week.
    History entries carried `fuels` for every week and a `whatif` block of
    TOTALS, so a windowed call fell back to the weekly figures and drew them
    under the window's own heading - weekly numbers labelled as annual. The
    components were never missing from the model, only from the payload:
    compute_week already returns the adjusted mix as r["adj"].

    Two derived figures ride with it, because the panel draws the saving as
    two blocks and neither can be recovered from the purchased mix alone:

      amb    the heat lifted from the ground - never bought, so it appears
             in no purchased total anywhere, and is the whole point of the
             comparison.
      avoid  boiler losses no longer incurred. It is the REMAINDER, derived
             as (saved - amb), not measured: it is whatever the fall in
             purchased energy is that the ground heat does not account for.
             Published so the page never has to compute it - and so the two
             blocks are guaranteed to close on the as-bought total rather
             than leaving an unexplained sliver.

    `avoid` is not clamped. On the present figures it is firmly positive
    (the route displaces more fuel than the pumps draw), but a week with
    almost no heating and heavy cooling could in principle invert it, and a
    silently clamped zero would hide that rather than show it. The front end
    guards instead.
    """
    adj = r["adj"]
    amb = (r["heat_repl_elec"] * (GEO_NETWORK_SCOP - 1.0)
           + r["cool_repl_elec"] * (PASSIVE_COOL_COP - 1.0))
    saved = r["total_in"] - r["new_total"]
    return {
        "gas": round(adj["gas"], 1),
        "oil": round(adj["oil"], 1),
        "bio_other": round(adj["bio"], 1),
        "solid": round(adj["solid"], 1),
        "heat_networks": round(adj["hn"], 1),
        "electricity": round(adj["elec"], 1),
        "amb": round(amb, 1),
        "avoid": round(saved - amb, 1),
    }


def build_history(prev, dd, base, slope, target, archive=None):
    """Maintain the rolling weekly ticker history (phase 1).
    Calendar weeks (Mon-Sun) where gas and degree days cover all 7 days,
    computed with compute_week()/compute_week_emissions(), priced at that
    week's cap, with that week's grid CI. Append-or-update by week_ending:
    weeks already stored are kept as-is except the 2 most recent (feed
    revisions). Retains every week: HISTORY_MAX is None from 20 Nov 2026, and
    any roll-off a future cap caused would be printed, not silent.

    ARCHIVE-SOURCED FROM 20 Nov 2026. Until then both inputs came from the
    LIVE fetches - gas from `target`, about 770 days, and degree days from
    `dd`, about 1,130 - so history could only ever cover the live window no
    matter how deep docs/daily.json ran. Passing the archive lets weeks be
    built across its whole span, which is what the 36m/48m/60m windows need.

    Two limits are structural rather than incidental, and both are stated on
    the page rather than worked around:

      TRAILING YEAR. Each week needs 365 days of degree days BEFORE it, to
      form the shape denominator compute_week divides by. So the earliest
      buildable week is archive start + 365 days, and a five-year archive
      yields four years of history, not five.

      THE CAP FLOOR. cap_prices() CLAMPS to its first row for any earlier
      date, so a 2022 week would silently be priced at the April 2024 cap and
      labelled 2024-04-01. Through the energy crisis that is not a small
      error. Weeks before CAP_HISTORY[0] are therefore stored with their
      energy and emissions and with every money field NULL."""
    hist_prev = {e["week_ending"]: e for e in (prev.get("history") or [])
                 if isinstance(e, dict) and e.get("week_ending")}
    schema_prev = prev.get("history_schema", 1)
    epoch_prev = prev.get("anchor_epoch", 0)
    cap_floor = CAP_HISTORY[0][0]

    # Degree days by date: the archive first, then the live fetch OVER it.
    # Same formula either way - max(0, base - gb_mean_temp) - and the archive
    # stores the same gb_mean_temp series fetch_degree_days produces, so the
    # two agree where they overlap. The live fetch still wins there because
    # it is the published source and carries the day's latest revision.
    _bf, _cbf = float(base), float(COOL_BASE)
    hdd_by_date, cdd_by_date = {}, {}
    arch_gas, arch_ci = {}, {}
    if archive and archive.get("dates"):
        _ad = archive["dates"]
        for d, t in zip(_ad, archive.get("gb_mean_temp") or []):
            if t is None:
                continue
            hdd_by_date[d] = round(max(0.0, _bf - t), 2)
            cdd_by_date[d] = round(max(0.0, t - _cbf), 2)
        arch_gas = {d: g for d, g in zip(_ad, archive.get("gas_GWh") or [])
                    if g is not None}
        arch_ci = {d: c for d, c in zip(_ad, archive.get("ci_g_kwh") or [])
                   if c is not None}
    hdd_by_date.update(zip(dd["dates"], dd["hdd"][base]))
    cdd_by_date.update(zip(dd["dates"], dd["cdd"][COOL_BASE]))

    # trailing-365d degree-day sums ending any date (prefix sums), over the
    # MERGED date axis - built from dd alone this could not reach back far
    # enough to give an archive-era week its denominator.
    dd_dates = sorted(hdd_by_date)
    cum_h, cum_c = [0.0], [0.0]
    for d in dd_dates:
        cum_h.append(cum_h[-1] + hdd_by_date[d])
        cum_c.append(cum_c[-1] + cdd_by_date.get(d, 0.0))
    pos = {d: i for i, d in enumerate(dd_dates)}

    def trailing_365(d, cum):
        j = pos[d] + 1
        i0 = max(0, j - 365)
        return cum[j] - cum[i0], j - i0

    # complete calendar weeks with gas + degree-day coverage, live or archived
    gas_days = set(target) | set(arch_gas)
    days_live = sorted(gas_days & set(hdd_by_date))
    weeks = {}
    for d in days_live:
        dtd = dt.date.fromisoformat(d)
        we = (dtd + dt.timedelta(days=6 - dtd.weekday())).isoformat()  # Sun
        weeks.setdefault(we, []).append(d)
    complete = [we for we in sorted(weeks) if len(weeks[we]) == 7]

    todo = sorted(set(w for w in complete if w not in hist_prev)
                  | set(complete[-2:]))
    # One-time restatement (schema 2, Jul 2026): indig_pct gains one
    # decimal - integer storage quantised the winter months of the 12m
    # line flat. Every stored week still inside the live gas window is
    # recomputed with its STORED grid_ci (no refetch) and the cap in
    # force; all other fields re-round identically. Weeks outside the
    # window keep their integer value - unrecomputable, and stated.
    restate = set()
    if (FORCE_RESTATE or schema_prev < HISTORY_SCHEMA
            or epoch_prev < ANCHOR_EPOCH):
        restate = set(w for w in complete if w in hist_prev) - set(todo)
        todo = sorted(set(todo) | restate)
    # A restatement was previously INVISIBLE in the log, so there was no way
    # to tell whether one had run, been skipped, or silently covered nothing.
    # Printed every run from 19 Aug 2026.
    print("history: schema %d -> %d, epoch %d -> %d | %d stored, %d complete, "
          "%d to compute of which %d restated"
          % (schema_prev, HISTORY_SCHEMA, epoch_prev, ANCHOR_EPOCH,
             len(hist_prev), len(complete), len(todo), len(restate)))
    built = dict(hist_prev)
    ci_failures = 0
    for we in todo:
        ds = sorted(weeks[we])
        if ds[-1] not in pos:
            continue
        h12, n_days = trailing_365(ds[-1], cum_h)
        if n_days < 365:
            continue   # can't form a trailing-12m shape denominator
        c12, _ = trailing_365(ds[-1], cum_c)
        stored = hist_prev.get(we)
        _arch_week = [arch_ci[d] for d in ds if d in arch_ci]
        if we in restate and stored and stored.get("grid_ci") is not None:
            week_ci = stored["grid_ci"]   # restatement: reuse, don't refetch
        elif len(_arch_week) == 7:
            # From the archive's daily CI. The archive and fetch_ci_weekly_mean
            # draw on the SAME NESO endpoint, and a weekly mean of seven daily
            # means equals the mean of the week's half-hours whenever the days
            # are equal length - so this reproduces the fetched figure except
            # on the two DST days a year, which hold 23 or 25 hours.
            #
            # It also matters that this does not fetch: backfilling four years
            # is about 210 weeks, and one API call each would be 210 calls per
            # build for data already sitting in docs/daily.json.
            week_ci = round(sum(_arch_week) / 7.0, 1)
        else:
            try:
                week_ci = fetch_ci_weekly_mean(ds[0], ds[-1])
            except Exception:
                traceback.print_exc()
                ci_failures += 1
                if ci_failures >= 3:   # API down; retry whole set next run
                    break
                continue
        space_wk = sum(max(0.0, slope * hdd_by_date[d]) for d in ds)
        hdd_wk = sum(hdd_by_date[d] for d in ds)
        cdd_wk = sum(cdd_by_date[d] for d in ds)
        p, cap_from = cap_prices(ds[-1])
        # PRICED NOW MEANS "A REAL PRICE WAS FOUND", not "inside the cap
        # table". Until 20 Nov 2026 cap_prices clamped, so a 2022 week would
        # have carried a bill at the April 2024 cap stamped 2024-04-01 - a
        # confident number for a period when households were on the Energy
        # Price Guarantee and not paying the cap at all. Those weeks were
        # therefore stored with every money field null.
        #
        # They are now priced from DOM_PRE_CAP_ANNUAL, which is QEP scaled
        # onto the cap basis. Only the 'fallback' basis - older than either
        # table - still drops the money, because that is the one case where
        # the figure would again be a clamp wearing a date.
        basis = p.get("_basis", "cap")
        priced = basis != "fallback"
        r = compute_week(space_wk, hdd_wk, cdd_wk, h12, c12, p)
        e = compute_week_emissions(r, week_ci)
        _m = (lambda v: round(v, 0)) if priced else (lambda v: None)
        built[we] = {
            "week_ending": we,
            "purchased_GWh": round(r["total_in"], 0),
            "heat_GWh": round(r["total_in"] - r["mix"]["cooling"]
                              - r["mix"]["ventilation"], 0),
            "cooling_GWh": round(r["cool_elec_wk"], 0),
            "ventilation_GWh": round(r["vent_elec_wk"], 0),
            "indig_pct": r["indig_services_now"],
            "bill_Mgbp": _m(sum(r["bill"].values())),
            "bill_heat_Mgbp": _m(sum(v for k, v in r["bill"].items()
                                     if k not in ("cooling",
                                                  "ventilation"))),
            "bill_cool_Mgbp": _m(r["bill"]["cooling"]),
            "emissions_kt": e["week_kt"],
            "emissions_heat_kt": e["week_heat_kt"],
            "emissions_cool_kt": e["week_cool_kt"],
            "whatif": {
                "purchased_GWh": round(r["new_total"], 0),
                "heat_GWh": round(r["wf_heat"], 0),
                "cooling_GWh": round(r["wf_cooling"], 0),
                "bill_heat_Mgbp": _m(r["wf_bill_heat"]),
                "bill_cool_Mgbp": _m(r["wf_bill_cool"]),
                "emissions_heat_kt": e["whatif_heat_kt"],
                "emissions_cool_kt": e["whatif_cool_kt"],
                "indig_pct": r["indig_services_20"],
                "bill_Mgbp": _m(r["bill_20"]),
                "emissions_kt": e["whatif_kt"],
            },
            "fuels": _fuel_block(r),
            "wf_fuels": _wf_fuel_block(r),
            "grid_ci": week_ci,
            # cap_from is None, not a clamped date, when no real price was
            # found: a stamp of 2024-04-01 on a 2022 week would read as
            # provenance when it is really the clamp.
            "cap_from": cap_from if priced else None,
            "priced": priced,
            # Which price series the money came from, and whether the
            # domestic rate is an annual mean. The page needs both: a bill
            # built on an annual average through the energy crisis is not
            # wrong, but it is a coarser thing than a bill built on that
            # quarter's cap, and the two sit on the same line.
            "price_basis": basis,
            "price_annual": bool(p.get("_annual")),
            "nd_from": p.get("_nd_from"),
        }
    ordered = [built[we] for we in sorted(built)]
    # HISTORY_MAX None means retain everything, the default from 20 Nov 2026.
    # The guard is not decoration: [-None:] raises, so the None case must be
    # taken before the slice. Any roll-off is printed, because this ran at the
    # cap for months discarding a week a week without saying so.
    if HISTORY_MAX is None or len(ordered) <= HISTORY_MAX:
        return ordered
    dropped = ordered[:len(ordered) - HISTORY_MAX]
    print("history: ROLLED OFF %d week(s), %s -> %s (cap %d). Rebuildable from "
          "the daily archive (gas, CI) plus CAP_HISTORY, for weeks the archive "
          "covers - check docs/daily.json before assuming."
          % (len(dropped), dropped[0]["week_ending"],
             dropped[-1]["week_ending"], HISTORY_MAX))
    return ordered[-HISTORY_MAX:]


def main():
    prev = load_previous()
    out = {"updated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "sources": {},
           # carried forward immediately so an early-error write can never
           # destroy the accumulated ticker history
           "history": prev.get("history") or []}

    try:
        # Reach back far enough to hold the WHOLE anchor year, whatever
        # today is: a fixed 940 days stops covering it after ~19 months.
        _anchor_span = (dt.date.today()
                        - dt.date(ANCHOR_YEAR, 1, 1)).days + 40
        # needs WINDOW_DAYS of weeks PLUS 365 before the oldest for the
        # trailing-12m shape denominator each week is computed against
        dd = fetch_degree_days(days=max(940, WINDOW_DAYS + 400,
                                        _anchor_span))
        out["sources"]["degree_days"] = {"status": "ok",
                                         "last_good": dd["dates"][-1]}
    except Exception:
        traceback.print_exc()
        dd = prev.get("_dd")
        out["sources"]["degree_days"] = {
            "status": "stale",
            "last_good": prev.get("sources", {}).get("degree_days", {}).get("last_good")}
        if not dd:
            _write(out | {"error": "no degree-day data available"})
            return

    try:
        # The National Gas API rejects a date range this long with a 400 -
        # found on the first 24-month run, 10 Aug 2026. Until fetch_gas
        # chunks by date, ask for the long window and fall back rather than
        # throw: a hard failure here freezes the whole weekly panel on stale
        # gas, which is far worse than a shorter history.
        gas = None
        for _span in (WINDOW_DAYS + 40, 405):
            try:
                gas = fetch_gas_demand(days=_span)
                if _span != WINDOW_DAYS + 40:
                    print("gas: %d-day request refused, served %d days instead "
                          "- history will build to what the feed allows"
                          % (WINDOW_DAYS + 40, _span))
                break
            except Exception as _e:
                if _span == 405:
                    raise
                print("gas: %d-day request failed (%s), retrying shorter"
                      % (_span, type(_e).__name__))
        target = gas["ldz_sum"]
        vals = sorted(target.values())
        print(f"gas diagnostics (LDZ sum): {len(target)} days, "
              f"{min(target)} to {max(target)}, min {vals[0]} max {vals[-1]} GWh")
        out["sources"]["gas"] = {"status": "ok",
                                 "last_good": max(target) if target else None,
                                 "meta": gas["_meta"]}
    except Exception:
        traceback.print_exc()
        gas = prev.get("_gas")
        out["sources"]["gas"] = {
            "status": "stale",
            "last_good": prev.get("sources", {}).get("gas", {}).get("last_good")}
        if not gas or "ldz_sum" not in gas:
            _write(out | {"error": "no gas data available"})
            return
        target = gas["ldz_sum"]

    nts = gas.get("nts_demand_actual", {})

    # spec build-order step 1: record the feed's true served window each run.
    # min(target) IS the empirical live/modelled boundary for gas-bound tiers.
    out["gas_window"] = {"oldest": min(target), "newest": max(target),
                         "days_served": len(target)}

    dd_idx = {d: i for i, d in enumerate(dd["dates"])}
    common = sorted(set(target) & set(dd_idx))[-WINDOW_DAYS:]

    # THE DAILY ARCHIVE, loaded ONCE for every consumer below. Until 20 Nov
    # 2026 the history block loaded it privately, thirty lines before it was
    # used, so the cooling panel could not see it at all: panel 4 was capped
    # at whatever the live degree-day fetch served - about 1,130 days - while
    # the archive held 1,827. The cap looked like WINDOW_DAYS + 5 and was
    # really the fetch. Loaded here instead, and still wrapped: a missing or
    # unreadable archive degrades every consumer to the old live-window
    # behaviour rather than failing the build.
    try:
        import daily_archive as _da_h
        archive = _da_h.load()
    except Exception:                                       # noqa: BLE001
        traceback.print_exc()
        archive = None
    if archive and archive.get("dates"):
        print("archive: %d days %s to %s"
              % (len(archive["dates"]), archive["dates"][0],
                 archive["dates"][-1]))
    else:
        archive = None
        print("archive: unavailable - panels fall back to the live fetch span")

    # Daily gas-and-temperature pair, dumped for offline analysis. Placed HERE
    # rather than beside the degree-day dicts because `gas` is fetched in this
    # function, not in build_history - a first attempt put it there and threw
    # NameError on every run (caught, so the build survived, but nothing was
    # written). `common` above is already the intersection of the two series,
    # so the pairing is done rather than redone.
    #
    # NOT used by anything on the build path: it exists so questions can be
    # asked of the gas/temperature pair that the kinked model forecloses - in
    # particular whether balance points are spread across the stock rather than
    # shared. See analysis/fit_balance_spread.py.
    # MERGED, NOT REWRITTEN, since 20 Nov 2026 - and the reason is narrower
    # than the first version of this comment claimed, so it is worth stating
    # exactly. This used to be json.dump onto a fresh dict built from `target`,
    # the live gas fetch, so the file only ever held what the feed had just
    # served. The fetch above asks for WINDOW_DAYS + 40 and FALLS BACK to 405
    # days when the feed refuses the long window.
    #
    # IN CI that fallback costs nothing persistent: update.yml does not commit
    # this file, the runner checks out fresh every night, and the same run that
    # writes it reads it in the next step. A short fetch means fit_balance_
    # spread sees fewer days THAT RUN, not a record that shrank.
    #
    # It is a real loss only where the file SURVIVES between runs, which means
    # a local build. There a short fetch would have replaced a long file with a
    # short one and said nothing. Merging costs four lines and removes the
    # difference between the two environments, which is worth more than the
    # narrow case it fixes: the date axis now only ever grows. Fresh values win
    # for days in both, because gas revises at D+1.
    #
    # docs/daily.json is the record of account and now carries gas_GWh and
    # gb_mean_temp itself, so the durable gas record no longer depends on this
    # file at all. This export stays because fit_balance_spread.py takes this
    # shape. It could be generated FROM the archive instead, which would make
    # it complete rather than fetch-limited - not done here because it changes
    # what that estimator is fitted on.
    try:
        _apath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "analysis")
        _gpath = os.path.join(_apath, "gas_daily.json")
        _pairs = {}
        try:                                   # existing file first
            with open(_gpath) as _f:
                _old = json.load(_f)
            for _d, _t, _g in zip(_old.get("dates") or [],
                                  _old.get("temp_C") or [],
                                  _old.get("gas_GWh") or []):
                if _t is not None and _g is not None:
                    _pairs[_d] = (_t, _g)
        except (OSError, ValueError):
            _old = None
        _was = len(_pairs)
        _tmap = dict(zip(dd["dates"], dd["gb_mean_temp"]))
        for _d in sorted(set(target) & set(dd_idx)):
            _t = _tmap.get(_d)
            _g = target.get(_d)
            if _t is not None and _g is not None:
                _pairs[_d] = (_t, _g)
        _ds = sorted(_pairs)
        _gt = {"dates": _ds,
               "temp_C": [_pairs[_d][0] for _d in _ds],
               "gas_GWh": [_pairs[_d][1] for _d in _ds]}
        os.makedirs(_apath, exist_ok=True)
        with open(_gpath, "w") as _f:
            json.dump(_gt, _f)
        print("gas_daily.json: %d paired days (%+d), %s to %s"
              % (len(_ds), len(_ds) - _was,
                 _ds[0] if _ds else "-", _ds[-1] if _ds else "-"))
    except Exception:
        traceback.print_exc()
    if len(common) < 90:
        _write(out | {"error": f"only {len(common)} overlapping days"})
        return

    y = [target[d] for d in common]

    best = None
    for base in HDD_BASES:
        x = [dd["hdd"][str(base)][dd_idx[d]] for d in common]
        slope, intercept, r2, resid_se = ols(x, y)
        if best is None or r2 > best["r2"]:
            best = {"base_temp": base, "slope_GWh_per_HDD": round(slope, 1),
                    "intercept_GWh": round(intercept, 1), "r2": round(r2, 3),
                    "resid_se_GWh": round(resid_se, 0),
                    "window_days": len(common), "target": "LDZ sum (buildings)"}
    base = str(best["base_temp"])
    hdd_series = [dd["hdd"][base][dd_idx[d]] for d in common]
    space_heat = [round(max(0.0, best["slope_GWh_per_HDD"] * h), 1)
                  for h in hdd_series]
    cdd_series = [dd["cdd"][COOL_BASE][dd_idx[d]] for d in common]

    # --- calibration (weather-normalised ECUK anchor) -------------------------
    # TRAILING TWELVE MONTHS. space_heat spans the whole regression window,
    # which is WINDOW_DAYS - two years since 11 Aug 2026 - so summing all of
    # it doubles the annual figure. This feeds the calibration ratio, the UK
    # national heat total, and the retro store's space_annual_twh, which is
    # the DENOMINATOR of the electrification limit: it is why the network
    # share halved from 98% to 49% on the first 24-month run.
    annual_space_twh = sum(space_heat[-ANNUAL_DAYS:]) / 1000.0
    hdd_all = dd["hdd"][base]
    _ay = str(ANCHOR_YEAR)
    _anchor_days = sum(1 for d_ in dd["dates"] if d_.startswith(_ay))
    hdd_2024 = sum(h for d_, h in zip(dd["dates"], hdd_all)
                   if d_.startswith(_ay))
    _full_year = 366 if ANCHOR_YEAR % 4 == 0 else 365
    _anchor_complete = _anchor_days >= _full_year
    # TRAILING TWELVE MONTHS, not the regression window. hdd_series spans
    # WINDOW_DAYS, which is now two years; summing all of it double-counts
    # the year and halves every share computed against it.
    hdd_12m = sum(hdd_series[-ANNUAL_DAYS:])
    anchor_gb = ECUK_UK_GAS_SPACE_HEAT_TWH_2024 * GB_SHARE_OF_UK_GAS_HEAT
    # Refuse to weather-normalise on a truncated reference year: a
    # partial anchor year understates its HDD, inflates the anchor and
    # flatters the ratio, all without any visible failure.
    if hdd_2024 and _anchor_complete:
        anchor_scaled = anchor_gb * (hdd_12m / hdd_2024)
    else:
        anchor_scaled = anchor_gb
        print("calibration: anchor year %s incomplete (%d/%d days) - "
              "weather normalisation SKIPPED" % (_ay, _anchor_days,
                                                 _full_year))
    # GATE. Four test suites passed on both sides of the 11 Aug window change
    # while every annual figure doubled, because they check structure and
    # internal consistency, not whether an annual quantity spans a year. GB
    # heating degree days run roughly 1,700-2,600 on a 15.5-16.5 base; twice a
    # year lands near 4,100, which is what shipped. Fail loudly instead.
    if not (1200.0 <= hdd_12m <= 3200.0):
        raise SystemExit(
            "calibration: hdd_trailing_12m = %.0f is outside the plausible "
            "annual band 1200-3200. Almost certainly a window-versus-year "
            "error: check that every trailing-12-month sum slices "
            "[-ANNUAL_DAYS:] rather than the whole regression window."
            % hdd_12m)
    ratio = annual_space_twh / anchor_scaled

    # ITEM 7 (Charlotte): publish a BAND, not a pass/fail flag.
    #
    # The ratio is reported against the base that wins on R2. That choice is
    # decided by almost nothing - on the 730-day window base 16.5 beats 15.5 by
    # 0.0009 of R2 - and it moves the answer by a tenth:
    #
    #     base   R2       ratio      (730-day window, 20 Nov 2026)
    #     14.5   0.9242   0.898
    #     15.5   0.9335   0.998
    #     16.5   0.9344   1.099   <- picked, and the gate passed by 0.001
    #
    # Two things follow. The published PASS was sitting one part in a thousand
    # inside a +/-10% gate. And the gate's tolerance is the SAME SIZE as the
    # base-selection spread it ignores, so it was never able to discriminate.
    #
    # The band is also far more stable than the point: 0.90-1.10 on every
    # window tried (730, 769, 1097 and 1827 days), while the winning base
    # FLIPS between 15.5 and 16.5 with window length. So the band is the
    # honest quantity and the point is an artefact of the selection.
    #
    # More record does not fix this. Across 769 -> 1827 days the R2 spread
    # between bases narrows 0.0119 -> 0.0070 and the ranking still changes.
    # This is the same shape as the cooling balance point: a near-flat
    # criterion over a parameter the answer is steep in.
    by_base = {}
    for _b in HDD_BASES:
        _hs = [dd["hdd"][str(_b)][dd_idx[d]] for d in common]
        _sl, _it, _r2, _ = ols(_hs, y)
        _ann = sum(max(0.0, _sl * h) for h in _hs[-ANNUAL_DAYS:]) / 1000.0
        _h12 = sum(_hs[-ANNUAL_DAYS:])
        # Anchor-year HDD from the WHOLE degree-day fetch, never the window:
        # a window that clips the anchor year inflates the scaled anchor
        # enormously (761 TWh on a 769-day window when taken from the window).
        _hay = sum(h for d_, h in zip(dd["dates"], dd["hdd"][str(_b)])
                   if d_.startswith(_ay))
        _sc = anchor_gb * (_h12 / _hay) if (_hay and _anchor_complete) \
            else anchor_gb
        by_base[str(_b)] = {"r2": round(_r2, 4),
                            "slope_GWh_per_HDD": round(_sl, 1),
                            "annual_TWh": round(_ann, 1),
                            "anchor_TWh": round(_sc, 1),
                            "ratio": round(_ann / _sc, 3)}
    _ratios = [v["ratio"] for v in by_base.values()]
    _r2s = [v["r2"] for v in by_base.values()]
    calibration = {
        "model_12m_gas_space_heat_TWh": round(annual_space_twh, 1),
        "ecuk_anchor_TWh": round(anchor_scaled, 1),
        "anchor_raw_UK_2024_TWh": ECUK_UK_GAS_SPACE_HEAT_TWH_2024,
        "gb_share": GB_SHARE_OF_UK_GAS_HEAT,
        "anchor_year": ANCHOR_YEAR,
        "anchor_year_days": _anchor_days,
        "anchor_year_complete": _anchor_complete,
        "hdd_2024": round(hdd_2024, 1),
        "hdd_trailing_12m": round(hdd_12m, 1),
        "annual_days_used": ANNUAL_DAYS,
        "anchor_status": ECUK_ANCHOR_STATUS,
        "ratio": round(ratio, 3),
        # The band, and what it is a band OVER: the HDD base, which the data
        # does not resolve. Not a confidence interval - it is the spread of
        # the answer across choices that fit the record equally well.
        "ratio_lo": round(min(_ratios), 3),
        "ratio_hi": round(max(_ratios), 3),
        "ratio_by_base": by_base,
        "base_r2_spread": round(max(_r2s) - min(_r2s), 4),
        "base_margin": round(sorted(_r2s)[-1] - sorted(_r2s)[-2], 4),
        # RETAINED, NOT RELIED ON. The flag stays in the payload so nothing
        # downstream breaks and so the old gate can still be seen, but the
        # page states the band instead: a +/-10% gate cannot discriminate
        # when the base-selection spread is itself about +/-10%.
        "within_10pct": abs(ratio - 1.0) <= 0.10,
        "gate_superseded_by_band": True,
    }

    # --- weekly gas headline ---------------------------------------------------
    wk = common[-7:]
    wk_i = [common.index(d) for d in wk]
    weekly = {
        "week_ending": wk[-1],
        "gas_total_GWh": round(sum(y[i] for i in wk_i), 0),
        "gas_space_heat_GWh": round(sum(space_heat[i] for i in wk_i), 0),
        "hdd_total": round(sum(hdd_series[i] for i in wk_i), 1),
        "cdd_total": round(sum(cdd_series[i] for i in wk_i), 1),
    }
    weekly["gas_baseline_GWh"] = round(
        weekly["gas_total_GWh"] - weekly["gas_space_heat_GWh"], 0)

    # --- live week: shared estimator + current cap period ---------------------
    p, cap_from = cap_prices(wk[-1])
    PRICE_TAG = (f"Ofgem price cap from {cap_from} (GB DD avg, incl VAT) "
                 "for domestic shares; services shares at QEP-anchored "
                 "non-domestic rates; oil/bio/network/solid prices are "
                 "estimates" + EST)
    cdd_12m = sum(cdd_series[-ANNUAL_DAYS:])   # trailing year, not the window
    r = compute_week(weekly["gas_space_heat_GWh"], weekly["hdd_total"],
                     weekly["cdd_total"], hdd_12m, cdd_12m, p)
    mix, useful = r["mix"], r["useful"]
    f_flat, f_h, f_c = r["f_flat"], r["f_h"], r["f_c"]
    # Per-fuel GB shares, not one gas-network factor for everything.
    A = {k: v * gb_share(k) * 1000.0 for k, v in ANNUAL_TWH.items()}  # GWh, GB

    weekly_mix = {
        "components_GWh": mix,
        "total_GWh": round(r["total"], 0),
        "combustion_share": round(r["combustion"] / r["total"], 3)
                            if r["total"] else None,
        "shape_factors": {"f_heating": round(f_h, 4),
                          "f_cooling": round(f_c, 4)},
        "note": ("Gas space heating is a live regression estimate; other "
                 "components are ECUK 2024 annual levels shaped by HDD/CDD. "
                 "Cooling & ventilation split 50% flat / 50% CDD-shaped "
                 "(assumption). Electric heating includes heat pump input "
                 "electricity only (ambient heat not yet counted)." + EST),
    }

    weekly_useful = {
        "components_GWh": useful,
        "total_GWh": round(sum(useful.values()), 0),
        "wasted_GWh": r["wasted"],
        "factors": {"boiler_gas": EFF["gas"], "oil": EFF["oil"],
                    "boiler_gas_dhw": EFF_DHW["gas"], "oil_dhw": EFF_DHW["oil"],
                    "bio": EFF["bio"], "solid": EFF["solid"],
                    "hp_spf": HP_SPF, "hp_elec_TWh_yr": HP_ELEC_TWH,
                    "cool_eer": COOL_EER,
                    "cool_eer_range": list(COOL_EER_RANGE),
                    "vent_share": VENT_SHARE,
                    "cool_share": COOL_SHARE,
                    "cool_base_C": float(COOL_BASE),
                    "split_source": (
                        "BEES (DECC/BEIS, fieldwork 2014-15) reports the "
                        "components ECUK merges: fans 6,030 GWh of electricity "
                        "against space cooling 5,090 plus humidification 130, "
                        "so 54% ventilation to 46% cooling. US CBECS 2018, "
                        "which separates the two natively, gives 56:44 from a "
                        "hotter climate. Ventilation has no COP - fans move "
                        "air, not heat - so it is reported as its own service "
                        "and is NOT in cooling delivered."),},
        "note": ("Useful basis: combustion derated by in-situ efficiencies; "
                 "heat pumps multiplied by SPF with ambient harvest shown "
                 "separately; cooling multiplied by EER on the weather-driven "
                 "half. Heat-network upstream losses excluded. Non-domestic "
                 "heat pumps not yet counted (understates ambient heat)." + EST),
    }

    # --- geothermal & ground-source panel --------------------------------------
    # All values TWh/yr useful heat, GB. Sources tagged; forecasts are
    # third-party pathways or explicitly-flagged Causeway derivations.
    # Revised Jul 2026 from capacity research. GSHP heat anchored on EGEC 2025
    # UK Country Update / Gonzalez Quiros et al. 2024: 1,430 GWhth/yr from
    # ~55,210 units, 847-861 MWth installed (2023 base year, sales-derived,
    # no later data published). Deep + mine water + open-loop district adds
    # ~0.05-0.1 TWh/yr (Gateshead 6 MWth, Eden 1.4 MWth ~1 GWh/yr,
    # Lanchester 3.6 MWth, Southampton no current data). EA 2024 note: only
    # ~30-38k of the ~55k units may be operational - hence range floor.
    GEO = {
        "today_gshp_TWh": 1.43,     # EGEC 2025 (2023 base); range 1.4-2.0
        "today_deep_TWh": 0.07,     # mine water + deep + open-loop district;
                                    # mid of 0.05-0.1 estimate
        "f2027_TWh": 1.7,           # 12-month trend: GSHP now ~1.3% of MCS HP
                                    # installs (413 of 30.6k H1 2025); modest
                                    # unit growth + Langarth/United Downs heat
        "f2031_TWh": 4.5,           # scenario: CCC 7th Carbon Budget pathway
                                    # (450k HP/yr by 2030) x assumed GSHP share
                                    # rising to ~5% + deep pipeline; range 3.5-6
        "f2050_TWh": 40.0,          # Project InnerSpace / REA / ARUP (Feb 2026):
                                    # 15 GWth ambition ~= ~40 TWh/yr heat
        "today_cool_TWh": 0.08,     # ATES ~8 MWth cold (11 systems, Jackson
                                    # et al. 2024) + Southampton hist. 7-8 GWh
                                    # + unmeasured reversible GSHP; range
                                    # 0.05-0.1 - no national statistic exists
    }
    geo_today = GEO["today_gshp_TWh"] + GEO["today_deep_TWh"]
    # Geothermal and heat pumps have no NI-specific driver, so they take the
    # household share rather than the gas-network factor.
    geo_week = (geo_today * GB_SHARE["default"] * 1000.0
                * (0.85 * f_h + 0.15 * f_flat))
    geo_cool_week = GEO["today_cool_TWh"] * GB_SHARE["default"] * 1000.0 * f_c
    heat_week_total = (mix["gas_space"] + mix["gas_dhw"] + mix["oil"]
                       + mix["elec_heat"] + mix["bio_other"]
                       + mix["heat_networks"] + mix["solid"])
    geothermal = {
        "week_GWh": round(geo_week, 0),
        "week_cool_GWh": round(geo_cool_week, 0),
        "cool_today_TWh": GEO["today_cool_TWh"],
        "week_share_of_heat": round(geo_week / heat_week_total, 4)
                              if heat_week_total else None,
        "annual_TWh": {
            "today": geo_today,
            "today_deep_only": GEO["today_deep_TWh"],
            "forecast_2027": GEO["f2027_TWh"],
            "forecast_2031": GEO["f2031_TWh"],
            "ambition_2050": GEO["f2050_TWh"],
        },
        "benchmark": ("Benchmark (EGEC Geothermal Market Report 2025): "
                      "Europe's 2.55 million geothermal heat pumps "
                      "delivered 88 TWh of heat in 2025. Sweden alone sold "
                      "26,785 units last year - the UK sold 4,070, with "
                      "twelve times Sweden's population."),
        "tags": {
            "today": "GSHP 1.43 TWh/yr: EGEC 2025 UK Country Update "
                     "(~55,210 units, 847-861 MWth, 2023 base) + ~0.07 "
                     "TWh/yr deep/mine/open-loop district (Gateshead 6 MWth, "
                     "Eden 1.4 MWth, Lanchester; Southampton no current "
                     "data). Range 1.4-2.0" + EST,
            "forecast_2027": "Trend: 4,070 UK GHP units sold in 2025 + 4 "
                             "new large closed-loop systems commissioned "
                             "(EGEC GMR 2025; MCS-certified subset far "
                             "smaller) + Langarth/United Downs pipeline" + EST,
            "forecast_2031": "Scenario: CCC Seventh Carbon Budget pathway "
                             "(450k HP/yr by 2030) x rising GSHP share + "
                             "deep pipeline (~11 UK geothermal DH systems in "
                             "development, EGEC GMR 2025) - Causeway "
                             "derivation, range 3.5-6" + EST,
            "ambition_2050": "Project InnerSpace / REA / ARUP, Feb 2026: "
                             "15 GWth by 2050",
        },
    }

    # --- the empty bar: installed hardware vs the 20% what-if ---------------
    # UK version of the Irish sibling's comparator: what serving 20% of
    # delivered buildings heat requires in installed thermal capacity at
    # 2,000 equivalent full-load hours †, against ground-source capacity
    # operating today and installed reality elsewhere. Comparator constants
    # are shared with the Irish sibling (EGEC 2025 basis); the gshp/deep
    # splits are approximate † - sync exact values from the Irish
    # data.json if the sibling re-anchors.
    try:
        elec_heat_all = ANNUAL_TWH["elec_space"] + ANNUAL_TWH["elec_dhw"]
        useful_heat_annual = (
            annual_space_twh * EFF["gas"]
            + ANNUAL_TWH["gas_dhw"] * EFF_DHW["gas"]
            + ANNUAL_TWH["oil_space"] * EFF["oil"]
            + ANNUAL_TWH["oil_dhw"] * EFF_DHW["oil"]
            + (ANNUAL_TWH["bio_space"] + ANNUAL_TWH["bio_dhw"]) * EFF["bio"]
            + ANNUAL_TWH["solid"] * EFF["solid"]
            + ANNUAL_TWH["heat_networks"] * EFF["heat_networks"]
            + max(0.0, elec_heat_all - HP_ELEC_TWH) * EFF["resistive"]
            + HP_ELEC_TWH * HP_SPF)
        # REPORTED OUTPUT, NOT A LOAD FACTOR. Until 20 Aug 2026 this block
        # converted installed capacity to annual output at a flat 2,000
        # equivalent full-load hours for every country. NO COUNTRY IN THE
        # COMPARISON RUNS 2,000 HOURS, and EGC 2025 Table 4 - the same paper
        # the comparator capacities already came from - reports the annual
        # production and the full-load hours alongside the capacity. The
        # column was simply not taken.
        #
        # The errors ran in OPPOSITE directions: the UK overstated by 20%,
        # the Netherlands by 83%, Sweden UNDERSTATED by 43%. So the gap
        # between the home country and the benchmark was drawn far narrower
        # than it is, and every error flattered the home country - which is
        # how it survived review on this site and the Irish sibling both.
        # Found by the Irish sibling, 20 Aug 2026.
        #
        # EGC 2025 Country Update Summary (Sanner, Antics, Baresi,
        # Urchueguia & Dumas), Table 4: Ground Source Heat Pump Use in
        # Europe in 2024. FLH is GWh/MWth and is carried so it can be
        # checked rather than trusted.
        REFERENCE_OUTPUT = {
            "United Kingdom": {"units": 55210, "MWth": 861,
                               "GWh": 1430, "flh": 1661},
            "France":         {"units": 209021, "MWth": 2293,
                               "GWh": 4750, "flh": 2072},
            "Netherlands":    {"units": 163169, "MWth": 2486,
                               "GWh": 2722, "flh": 1095},
            "Sweden":         {"units": 690000, "MWth": 8120,
                               "GWh": 28400, "flh": 3498},
        }
        REFERENCE_SOURCE = ("EGC 2025 Country Update Summary (Sanner, "
                            "Antics, Baresi, Urchueguia & Dumas), Table 4: "
                            "Ground Source Heat Pump Use in Europe in 2024")
        # The what-if uses the UK's OWN full-load hours, not a European
        # convention. Fewer hours means MORE capacity to serve the same
        # heat, so this moves against the site's own interest - which is
        # the reason to trust it.
        UK_FLH = REFERENCE_OUTPUT["United Kingdom"]["flh"]
        whatif_MWth = 0.20 * useful_heat_annual * 1e6 / UK_FLH
        UK_GSHP_MWTH, UK_DEEP_MWTH = 861.0, 10.0
        geothermal["hardware"] = {
            "uk_gshp_MWth": UK_GSHP_MWTH,        # EGC 2025 CU Table 4:
                                                 # 55,210 units, 861 MWth
            "uk_deep_MWth": UK_DEEP_MWTH,        # Gateshead 6 + Eden 1.4 +
                                                 # others, order-of † (EGC
                                                 # Table 3 reports only 1.4)
            "whatif_MWth": round(whatif_MWth, 0),
            "reference_output": REFERENCE_OUTPUT,
            "reference_source": REFERENCE_SOURCE,
            "uk_flh": UK_FLH,
            "useful_heat_annual_TWh": round(useful_heat_annual, 1),
            "per_person_W": round((UK_GSHP_MWTH + UK_DEEP_MWTH) * 1e6
                                  / (UK_POP_M * 1e6), 1),
            "sweden_per_person_W": 773,          # 8,120 MWth / 10.5m
            "sales_2025": {"uk": 4070, "sweden": 26785},   # EGEC GMR 2025
            # EGC 2025 country updates (Sanner et al.), Tables 3+4,
            # end-2024 - the Irish sibling's totals resolve to exactly
            # these: GSHP capacity + deep direct use. NL deep is almost
            # entirely greenhouses (agri 363 of 367 MWth; DH only 3.8) -
            # stated in the note.
            "comparators": [
                {"name": "France", "gshp_MWth": 2293, "deep_MWth": 724},
                {"name": "Netherlands", "gshp_MWth": 2486, "deep_MWth": 367},
                {"name": "Sweden", "gshp_MWth": 8120, "deep_MWth": 47},
            ],
            "note": ("Installed thermal capacity basis. The what-if bar "
                     "prices the 20% strip in hardware at the UK fleet's "
                     "own " + str(int(UK_FLH)) + " equivalent full-load "
                     "hours, reported rather than assumed. Comparator "
                     "capacities and output: EGC 2025 country updates "
                     "(Sanner et al., Tables 3-4, end-2024) - GSHP fleet "
                     "plus deep direct use; the Netherlands' deep "
                     "capacity is almost entirely greenhouse heat, not "
                     "buildings."),
        }
        # Calibration: the same fleets as a share of each country's OWN
        # buildings heat, computed from REPORTED annual output rather than
        # from capacity times a load factor. National heat anchors are
        # input-basis estimates †: UK live from this model (space + DHW);
        # France ~350 TWh (IEA/Heat Roadmap order), Netherlands ~115 TWh
        # (residential space heating alone 85 TWh in 2016 + DHW +
        # services), Sweden ~80 TWh (Swedish Energy Agency / ODYSSEE,
        # residential + services heat). Delivered-vs-input mismatch
        # (~15-20%) accepted at this precision and stated.
        uk_nat_heat = (annual_space_twh
                       + ANNUAL_TWH["elec_space"] + ANNUAL_TWH["oil_space"]
                       + ANNUAL_TWH["bio_space"] + ANNUAL_TWH["heat_networks"]
                       + ANNUAL_TWH["solid"]
                       + ANNUAL_TWH["gas_dhw"] + ANNUAL_TWH["elec_dhw"]
                       + ANNUAL_TWH["oil_dhw"] + ANNUAL_TWH["bio_dhw"])
        # ONE DEFINITION OF "UK HEAT", STATED ONCE. Raised in peer review on
        # 19 Nov 2026: the site ran two scenarios under one label. Section 5
        # sized a 20% what-if on DELIVERED heat (72.9 TWh at 1,661 h, so
        # 43.9 GWth); section 6 appraised 87.2 TWh at 2,717 h; the
        # methodology stated 52 GWth. All three were called "20%", and
        # nothing on the site said why they differ.
        #
        # They differ because the denominators do. Both of the site's own
        # cover the SAME scope - buildings space heat plus hot water, all
        # fuels - and differ only by conversion loss: input is fuel bought,
        # delivered is heat received at the building.
        geothermal["hardware"]["denominators"] = {
            "scope": "UK buildings: space heating plus hot water, all fuels. "
                     "Industry and process heat are outside it.",
            "input_TWh": round(uk_nat_heat, 1),
            "delivered_TWh": round(useful_heat_annual, 1),
            "delivered_over_input": round(useful_heat_annual / uk_nat_heat, 4),
            "twenty_pct_of_delivered_TWh": round(0.20 * useful_heat_annual, 1),
            "twenty_pct_of_input_TWh": round(0.20 * uk_nat_heat, 1),
            "note": "Every heat and capacity figure on this site traces to "
                    "one of these two bases. A share of one is not a share "
                    "of the other.",
            "uses": {
                "panel5_whatif": "20% of DELIVERED, at the UK fleet's own "
                                 "1,661 full-load hours",
                "country_shares": "INPUT, because the comparator national "
                                  "totals are input-basis estimates",
                "panel6_programme": "NEITHER. Section 6 appraises the "
                                    "government's heat network commitment "
                                    "(87.2 TWh of delivered heat), which is "
                                    "about a fifth of UK heat on the "
                                    "government's own wider denominator. "
                                    "Against delivered_TWh here it is 23.9%. "
                                    "The two '20%' figures on this site are "
                                    "two scenarios, not one.",
            },
        }
        NAT_HEAT_TWH = {"France": 350.0, "Netherlands": 115.0,
                        "Sweden": 80.0}
        # Reported GWh over national heat. No load-hour term appears here:
        # test_panel5.js fails if one reappears, and is proven to catch it
        # because restoring the 2,000-hour convention returns Sweden to the
        # 20.4% this site published before the error was found.
        shares = [{"name": "United Kingdom",
                   "national_heat_TWh": round(uk_nat_heat, 0),
                   "reported_GWh": REFERENCE_OUTPUT["United Kingdom"]["GWh"],
                   "share_pct": round(
                       100 * REFERENCE_OUTPUT["United Kingdom"]["GWh"] / 1000.0
                       / uk_nat_heat, 2)}]
        for c in geothermal["hardware"]["comparators"]:
            nat = NAT_HEAT_TWH[c["name"]]
            g = REFERENCE_OUTPUT[c["name"]]["GWh"]
            shares.append({"name": c["name"], "national_heat_TWh": nat,
                           "reported_GWh": g,
                           "share_pct": round(100 * g / 1000.0 / nat, 2)})
        geothermal["hardware"]["share_of_national_heat"] = {
            "countries": shares,
            "whatif_pct": 20.0,
            "boiler_flow_note": ("For capacity scale: the UK replaces "
                                 "roughly 1.6 million boilers a year - of "
                                 "order 35-40 GW of combustion capacity "
                                 "installed annually" + EST + "; the "
                                 "what-if is about one year of that "
                                 "replacement flow, redirected."),
            "note": ("Each country's installed geothermal fleet at " +
                     str(int(UK_FLH)) + " equivalent full-load hours" + EST +
                     ", as a share of its own buildings heat (space + hot "
                     "water, input basis; national anchors are estimates" +
                     EST + " - UK live from this model, others of order "
                     "IEA/ODYSSEE/Heat Roadmap). Sweden's fleet already "
                     "serves about the share the UK what-if proposes."),
        }
    except Exception:
        traceback.print_exc()
        geothermal["hardware"] = (prev.get("geothermal") or {}).get("hardware")

    # --- cost layer (4a): household p/kWh useful + national weekly bill --------
    household = [
        {"route": "Gas boiler", "p_per_useful_kwh":
            round(p["gas"] / EFF["gas"], 1),
         "basis": "cap gas rate / 0.835 in-situ efficiency"},
        {"route": "Oil boiler", "p_per_useful_kwh":
            round(p["oil"] / EFF["oil"], 1),
         "basis": "est. kerosene / 0.82" + EST},
        {"route": "Resistive electric", "p_per_useful_kwh":
            round(p["elec"], 1),
         "basis": "cap electricity rate, COP 1"},
        {"route": "Air-source heat pump", "p_per_useful_kwh":
            round(p["elec"] / ASHP_SPF, 1),
         "basis": "cap electricity / SPF 2.80 (ESC field data)"},
        {"route": "Ground-source / geothermal", "p_per_useful_kwh":
            round(p["elec"] / GSHP_SPF, 1),
         "basis": "cap electricity / SPF 3.24 (ESC field data)"},
        {"route": "Geothermal heat/cool network", "p_per_useful_kwh":
            round(p["elec"] / GEO_NETWORK_SCOP, 1),
         "basis": "cap electricity / SCOP 5.0 (networked ambient loop, "
                  "shared boreholes/aquifer)" + EST},
        {"route": "Passive ground cooling", "p_per_useful_kwh":
            round(p["elec"] / PASSIVE_COOL_COP, 1),
         "basis": "cap electricity / COP ~20 (circulation only)" + EST},
    ]

    whatif = {
        "useful_heat_GWh": round(r["useful_heat_wk"], 0),
        "useful_cool_GWh": useful["cooling_delivered"],
        "cost_Mgbp": round(r["whatif_heat_m"] + r["whatif_cool_m"], 0),
        "heat_Mgbp": round(r["whatif_heat_m"], 0),
        "cool_Mgbp": round(r["whatif_cool_m"], 0),
        "assumptions": ("Illustrative Causeway what-if: identical useful heat "
                        "and cooling delivered via geothermal networks - heat "
                        "at SCOP 5.0, cooling passively at COP ~20, current "
                        "capped electricity price. Running cost only; no "
                        "capex, network build, or price feedbacks." + EST),
    }

    cost = {
        "price_tag": PRICE_TAG,
        "household_p_per_useful_kwh": household,
        "gshp_vs_gas_boiler": {
            "gshp": round(p["elec"] / GSHP_SPF, 1),
            "gas_boiler": round(p["gas"] / EFF["gas"], 1),
            "gshp_cheaper": (p["elec"] / GSHP_SPF) < (p["gas"] / EFF["gas"]),
        },
        "national_week_Mgbp": r["bill"],
        "national_week_heat_Mgbp": r["bill_heat"],
        "national_week_cool_Mgbp": r["bill_cool"],
        "national_week_total_Mgbp": round(sum(r["bill"].values()), 0),
        "whatif_geothermal": whatif,
        "note": ("Running cost only: no capex, grants, or standing charges. "
                 "Gas and electricity priced by sector: ECUK domestic shares at "
                 "the Ofgem cap, services shares at QEP-anchored "
                 "non-domestic rates (estimates \u2020). "
                 "The electricity/gas price ratio embeds policy levies on "
                 "electricity; rebalancing would shift these comparisons "
                 "further toward heat pumps."),
    }

    # WHAT-IF BARS. The energy balance can now toggle between what Britain
    # actually bought this week and what the 20% geothermal what-if would have
    # bought for the same delivered service. The per-fuel split already existed
    # inside the what-if arithmetic (adj) - it simply was not published, so the
    # front end could show totals but not composition.
    #
    # The OUT side barely moves, by construction: the what-if delivers the same
    # heat and the same cooling. What changes is the IN side and the waste - a
    # fifth of the combustion goes, and with it the flue losses, replaced by a
    # much smaller quantity of electricity plus ambient heat harvested from the
    # ground. That is the whole argument in one chart.
    _wf = r["adj"]
    # Ambient harvested = service delivered minus electricity bought, for both
    # legs. Derived from the replacement electricities rather than the raw
    # service totals, because those are locals of the calculation function -
    # heat_repl_elec = service x R / SCOP, so service x R = elec x SCOP.
    _wf_ambient = (r["heat_repl_elec"] * (GEO_NETWORK_SCOP - 1.0)
                   + r["cool_repl_elec"] * (PASSIVE_COOL_COP - 1.0))
    whatif_bars = {
        "mix": {
            "gas": round(_wf["gas"], 0), "oil": round(_wf["oil"], 0),
            "bio_other": round(_wf["bio"], 0), "solid": round(_wf["solid"], 0),
            "heat_networks": round(_wf["hn"], 0),
            "electricity": round(_wf["elec"], 0),
        },
        "mix_total_GWh": round(sum(_wf.values()), 0),
        "geothermal_ambient_GWh": round(_wf_ambient, 0),
        "useful_total_GWh": round(sum(r["useful"].values()), 0),
        "share_pct": round(100 * R_SHIFT),
        "note": ("The same delivered heat and cooling, with a fifth of it "
                 "served by geothermal networks instead. The useful side is "
                 "unchanged by construction - that is the point of the "
                 "comparison. What moves is what had to be bought to deliver "
                 "it, and how much was thrown away doing so." + EST),
    }

    # --- headline stats: indigenous share + 20% geothermal what-if -------------
    headlines = {
        "purchased_GWh": round(r["total_in"], 0),
        "heat_GWh": round(r["total_in"] - r["mix"]["cooling"]
                              - r["mix"]["ventilation"], 0),
        "cooling_GWh": round(r["cool_elec_wk"], 0),
        "ventilation_GWh": round(r["vent_elec_wk"], 0),
        "indigenous_pct": round(r["indig_services_now"]),  # services basis (hero, int)
        "indigenous_basis": "services",
        "indigenous_purchased_pct": r["indig_now"],   # purchased basis (methods)
        "whatif_20pct_geothermal": {
            "purchased_GWh": round(r["new_total"], 0),
            "heat_GWh": round(r["wf_heat"], 0),
            "cooling_GWh": round(r["wf_cooling"], 0),
            "bill_heat_Mgbp": round(r["wf_bill_heat"], 0),
            "bill_cool_Mgbp": round(r["wf_bill_cool"], 0),
            "indigenous_pct": round(r["indig_services_20"]),  # services basis (hero, int)
            "indigenous_purchased_pct": r["indig_20"],
            "bill_Mgbp": round(r["bill_20"], 0),
            # the route's OWN electricity, so the saving can be seen to be net
            "route_elec_GWh": r["wf_route_elec_wk"],
        },
        "indig_note": ("Indigenous share is measured on a SERVICES basis: "
                       "the UK-origin share of useful heat and cooling "
                       "delivered. Each service inherits the origin of its "
                       "energy input (gas ~42% UK-origin, electricity ~75%, "
                       "others flagged estimates" + EST + "); harvested "
                       "ambient/ground heat counts as 100% indigenous, "
                       "consistent with Eurostat/DUKES renewable-supply "
                       "accounting. Cooling's delivered multiple inherits "
                       "its electricity's share - thermodynamic leverage is "
                       "not an energy origin. On the purchased-energy basis "
                       "the shares are lower and barely move with "
                       "geothermal, because ground heat is never purchased: "
                       "that is the point. 20% what-if: one-fifth of heat "
                       "via SCOP-5 networks (80% ground heat) - SCOP 5 is a "
                       "blended estimate across depths and system types, "
                       "including seasonally balanced / UTES-coupled "
                       "networks, so storage and recovery of rejected heat "
                       "are factored in rather than additional" + EST +
                       " - one-fifth of cooling near-passive at "
                       "COP 20" + EST + "."),
    }

    # --- daily heat spark gap (wholesale basis; optional feeds) ----------------
    spark = None
    try:
        sap = fetch_gas_sap()
        mid = fetch_elec_mid()
        gas_heat = sap["gbp_per_mwh"] / EFF["gas"]
        hp_heat = mid["gbp_per_mwh"] / GSHP_SPF
        # BOTH BASES, 12 Aug 2026. The ticker ran on wholesale alone, and
        # the bill panels run on retail, so the page carried two answers to
        # what looked like one question. Publishing both turns that into the
        # point: the wholesale gap is roughly three times the retail one, and
        # the difference is policy and network cost loaded onto electricity
        # rather than gas.
        _rp, _ = cap_prices(dt.date.today().isoformat())
        rt_gas = _rp["gas"] / EFF["gas"]              # p per useful kWh
        rt_gshp = _rp["elec"] / GSHP_SPF
        spark = {
            "date": max(sap["date"], mid["date"]),
            "gas_boiler_heat_gbp_mwh": round(gas_heat, 1),
            "gshp_heat_gbp_mwh": round(hp_heat, 1),
            "gap_gbp_mwh": round(gas_heat - hp_heat, 1),
            "retail": {
                "gas_boiler_p_per_kwh": round(rt_gas, 1),
                "gshp_p_per_kwh": round(rt_gshp, 1),
                "gap_p_per_kwh": round(rt_gas - rt_gshp, 1),
                "gap_gbp_mwh": round((rt_gas - rt_gshp) * 10, 1),
            },
            "basis": ("Wholesale daily: gas SAP / 0.835 boiler efficiency vs "
                      "electricity market index / SPF 3.24. Commodity cost "
                      "only - excludes network, policy and supply costs. The "
                      "retail line is the same two routes at the Ofgem cap, "
                      "which includes them - the difference between the two "
                      "gaps is what policy and network costs do to the "
                      "comparison, and they fall much harder on electricity "
                      "than on gas. A flat daily electricity mean also "
                      "understates heat-pump cost by about 3%, because a heat "
                      "pump buys most of its power at the morning and evening "
                      "peaks - measured, and logged as the coincidence "
                      "premium."),
        }
        out["sources"]["prices"] = {"status": "ok", "last_good": spark["date"]}
    except Exception:
        traceback.print_exc()
        spark = (prev.get("spark") or None)
        out["sources"]["prices"] = {
            "status": "stale" if spark else "unavailable",
            "last_good": spark.get("date") if spark else None}

    # --- Northern Ireland summary ----------------------------------------------
    # NI is on separate gas (mutual networks, no GB LDZ) and electricity (SEM)
    # systems - the live GB feeds above do not cover it.
    NI_ANNUAL_HEAT_TWH = 14.0   # Causeway estimate - refine from DfE
                                # 'Energy in Northern Ireland'
                                # Oil share sourced: NISRA CHS 2024/25
    ni_hdd = dd["ni"]["hdd_15_5"]
    ni_wk_hdd = round(sum(ni_hdd[-7:]), 1) if len(ni_hdd) >= 7 else None
    ni_12m = sum(ni_hdd[-365:]) if len(ni_hdd) >= 300 else None
    ni_f = (sum(ni_hdd[-7:]) / ni_12m) if ni_12m else 0.0
    ni_week_heat = NI_ANNUAL_HEAT_TWH * 1000.0 * (0.8 * ni_f + 0.2 * f_flat)
    ni_panel = {
        "week_hdd": ni_wk_hdd,
        "week_heat_GWh_est": round(ni_week_heat, 0),
        "annual_TWh_est": NI_ANNUAL_HEAT_TWH,
        "est_mark": True,
        "oil_share_note": ("just over 60% of NI homes heat with oil, "
                           "gas 36% and rising (NISRA CHS 2024/25)"),
        "why_separate": ("NI runs on separate gas and electricity systems "
                         "(no GB LDZ, SEM market), so the live GB feeds "
                         "on this page do not cover it - NI is estimated "
                         "from annual statistics shaped by NI degree days."),
    }

    # --- sibling comparator: island of Ireland cross-calibration ------------
    # Reference constants from the July 2026 cross-calibration exchange with
    # the Irish Heat Split (sibling site). Conventions synced in that
    # exchange: heat is INPUT energy, buildings-only (no industrial
    # process), space HDD-shaped, DHW flat at 7/365. The cooling scopes
    # DIFFER by design (island counts the cold economy; UK counts ECUK
    # comfort/ventilation) so cooling is expressly NOT compared. UK
    # population per the exchange's convention (68m, rounded) †.
    IRISH_SIBLING = {"heat_input_TWh": 43.8, "pop_m": 7.1,
                     "cold_economy_TWh": 12.1, "as_of": "2026-07"}
    sibling = None
    try:
        space_nongas = (ANNUAL_TWH["elec_space"] + ANNUAL_TWH["oil_space"]
                        + ANNUAL_TWH["bio_space"]
                        + ANNUAL_TWH["heat_networks"] + ANNUAL_TWH["solid"])
        dhw_all = (ANNUAL_TWH["gas_dhw"] + ANNUAL_TWH["elec_dhw"]
                   + ANNUAL_TWH["oil_dhw"] + ANNUAL_TWH["bio_dhw"])
        uk_space = annual_space_twh + space_nongas
        uk_total = uk_space + dhw_all
        uk_pc = uk_total / UK_POP_M
        isl_pc = IRISH_SIBLING["heat_input_TWh"] / IRISH_SIBLING["pop_m"]
        ratio = isl_pc / uk_pc
        sibling = {
            "uk": {"heat_input_TWh": round(uk_total, 1),
                   "space_TWh": round(uk_space, 1),
                   "dhw_TWh": round(dhw_all, 1),
                   "dhw_share": round(dhw_all / uk_total, 3),
                   "pop_m": UK_POP_M,
                   "per_capita_MWh": round(uk_pc, 2)},
            "island": dict(IRISH_SIBLING,
                           per_capita_MWh=round(isl_pc, 2)),
            "ratio_island_over_uk": round(ratio, 2),
            "gate": [0.8, 1.2],
            "within_gate": 0.8 <= ratio <= 1.2,
            "conventions": ("Synced with the Irish Heat Split, July 2026: "
                            "heat input basis, buildings only (no "
                            "industrial process heat), space shaped by "
                            "HDD, hot water flat at 7/365. UK gas space "
                            "from the live 12-month regression; other "
                            "fuels at ECUK anchors" + EST + "."),
            "cooling_note": ("Cooling is NOT compared: the island figure "
                             "counts the cold economy (12.1 TWh - data "
                             "centres, refrigeration, process), the UK "
                             "figure counts ECUK comfort & ventilation "
                             "(10.4 TWh) - different scopes by design. "
                             "A UK cold-economy layer is staged after "
                             "the cooling reconciliation lands; until "
                             "then both sites gate the comparison behind "
                             "this caveat."),
            "sibling_url": "https://causewaygt.github.io/irish-heatsplit/",
        }
    except Exception:
        traceback.print_exc()
        sibling = prev.get("sibling")

    # --- carbon layer -----------------------------------------------------------
    # Electricity: live GB grid intensity (NESO Carbon Intensity API,
    # trailing-7-day mean of half-hourly actuals).
    carbon = None
    try:
        ci = fetch_carbon_intensity(days=7)
        grid_ci = ci["g_per_kwh"]
        out["sources"]["carbon"] = {"status": "ok", "last_good": ci["to"]}
    except Exception:
        traceback.print_exc()
        prev_c = prev.get("carbon") or {}
        grid_ci = prev_c.get("grid_ci_g_per_kwh")
        out["sources"]["carbon"] = {
            "status": "stale" if grid_ci else "unavailable",
            "last_good": prev.get("sources", {}).get("carbon", {}).get("last_good")}
    if grid_ci:
        e = compute_week_emissions(r, grid_ci)
        # per useful kWh, g:
        routes = [
            {"route": "Gas boiler", "g_per_useful_kwh":
                round(CF["gas"] / EFF["gas"], 0)},
            {"route": "Oil boiler", "g_per_useful_kwh":
                round(CF["oil"] / EFF["oil"], 0)},
            {"route": "Resistive electric", "g_per_useful_kwh":
                round(grid_ci, 0)},
            {"route": "Air-source heat pump", "g_per_useful_kwh":
                round(grid_ci / ASHP_SPF, 0)},
            {"route": "Ground-source / geothermal", "g_per_useful_kwh":
                round(grid_ci / GSHP_SPF, 0)},
            {"route": "Geothermal heat/cool network", "g_per_useful_kwh":
                round(grid_ci / GEO_NETWORK_SCOP, 0)},
        ]
        carbon = {
            "grid_ci_g_per_kwh": grid_ci,
            "week_kt": e["week_kt"],
            "week_heat_kt": e["week_heat_kt"],
            "week_cool_kt": e["week_cool_kt"],
            "whatif_20pct_kt": e["whatif_kt"],
            "whatif_heat_kt": e["whatif_heat_kt"],
            "whatif_cool_kt": e["whatif_cool_kt"],
            "whatif_saving_kt": e["saving_kt"],
            "routes_g_per_useful_kwh": routes,
            "note": ("Combustion factors: DESNZ GHG conversion factors 2025 "
                     "(natural gas 0.18296 kgCO2e/kWh = 183 g, gross calorific "
                     "value basis, consistent with UK metered gas "
                     "billing; kerosene ~247, coal ~345 gCO2e/kWh). "
                     "Bioenergy counted at zero combustion emissions "
                     "(biogenic convention; supply chain excluded). Heat "
                     "networks assumed gas-fired" + EST + ". Electricity at "
                     "the live GB grid intensity (NESO Carbon Intensity API, "
                     "7-day mean) - so the heat-pump rows fall every year "
                     "the grid decarbonises, while combustion never does."),
        }

    # --- ticker history (phase 1): live weekly hero four + what-if twins -------
    try:
        # ORDER MATTERS. These markers are what tell the NEXT run that a
        # restatement has already happened, so they must be written only
        # after build_history has actually succeeded. Written first, a
        # throw inside build_history - and a 110-week restatement pass is
        # exactly when one is likely - would advance the epoch while the
        # history fell back to the carried-forward copy, and the correction
        # would never be retried. Reordered 19 Aug 2026.
        # The archive is read in main(), not in build_history, so a missing
        # or unreadable one degrades to exactly the old live-window behaviour
        # rather than failing the build. Hoisted to one load 20 Nov 2026.
        _hist = build_history(prev, dd, base,
                              best["slope_GWh_per_HDD"], target, archive)
        out["history"] = _hist
        # WHAT THE PAGE IS ALLOWED TO OFFER. The window buttons are built from
        # this rather than hard-coded, because the span is data: a five-year
        # archive yields four years of history (each week needs a trailing
        # year of degree days before it), so a 60m button would be empty
        # today and correct after one more year of backfill. Stating a window
        # the record does not cover is the same fault as stating a number the
        # page does not read.
        out["history_weeks"] = len(_hist)
        out["history_priced_from"] = next(
            (w["week_ending"] for w in _hist if w.get("priced")), None)
        # Where the domestic price stops being that quarter's cap and becomes
        # an annual mean. The page says so on any window that reaches back
        # into it: a bill on an annual average through the energy crisis is
        # not wrong, but it is coarser than a bill on that quarter's cap, and
        # both sit on one line.
        _ann = [w["week_ending"] for w in _hist if w.get("price_annual")]
        out["history_annual_price_to"] = _ann[-1] if _ann else None
        out["history_annual_price_weeks"] = len(_ann)
        out["history_schema"] = HISTORY_SCHEMA
        out["anchor_epoch"] = ANCHOR_EPOCH
        out["history_note"] = (
            "Weekly hero-number history for the ticker. Calendar weeks "
            "(Mon-Sun) computed with the same estimators as the live "
            "panels, only where the gas feed served all 7 days - live by "
            "construction, no modelling. Priced at the Ofgem cap in force "
            "that week; emissions at that week's mean grid intensity "
            "(NESO CI API). Estimator constants (ECUK vintage, INDIG "
            "shares, non-cap and non-domestic prices, sector shares) are "
            "today's, applied uniformly; "
            "entries older than the two most recent weeks are frozen as "
            "first computed.")
    except Exception:
        traceback.print_exc()
        # keep the carried-forward history from the top of main()

    # --- observed cooling: demand vs delivery (CDD saturation analysis) --------
    # Summer daily underlying electricity demand (NESO ND + embedded solar +
    # wind reconstructed) binned by CDD(18). Low-bin slope extrapolated
    # linearly = latent cooling demand; observed bin means = delivered.
    # Divergence = installed-capacity saturation, measured not assumed.
    # DOES NOT yet replace the ECUK-shaped cooling in the bill/carbon chain -
    # reconciliation over a full summer first.
    cooling_observed = None
    elec = None   # also read by the reconciliation diagnostic below
    try:
        this_year = dt.date.today().year
        # three calendar years: the curve only needs two summers, but the
        # daily tier series behind the 24-month view needs the days as well
        elec = fetch_daily_underlying_demand(
            [this_year - 2, this_year - 1, this_year])
        out["sources"]["electricity"] = {
            "status": _recency("ok", max(elec), lag_ok_days=7),
            "last_good": max(elec),
            "note": "NESO demand publishes on a lag; historic file "
                    "refreshed periodically, update feed ~daily"}
        # summer subset (May-Sep), weekend-adjusted
        cdd_by_date = {d_: dd["cdd"][COOL_BASE][dd_idx[d_]]
                       for d_ in elec if d_ in dd_idx}
        summer = [d_ for d_ in cdd_by_date
                  if 5 <= int(d_[5:7]) <= 9]
        if len(summer) >= 60:
            # demean within (month, weekend-class) cells: hot-vs-cool
            # contrasts are made within the same month, removing the
            # holiday/seasonal baseline confound (Aug demand is depressed
            # exactly when CDD is highest)
            cells = {}
            for d_ in summer:
                dtd = dt.date.fromisoformat(d_)
                key = (d_[5:7], dtd.weekday() >= 5)
                cells.setdefault(key, []).append(d_)
            anomaly = {}
            for key, days in cells.items():
                if len(days) < 4:
                    continue
                mean = sum(elec[d_] for d_ in days) / len(days)
                for d_ in days:
                    anomaly[d_] = elec[d_] - mean
            # bin anomalies by CDD
            bins = {}
            for d_, v in anomaly.items():
                c = cdd_by_date[d_]
                b = 0 if c == 0 else min(5, int(c) + 1)
                bins.setdefault(b, []).append(v)
            bin_mean = {b: sum(v) / len(v) for b, v in bins.items()
                        if len(v) >= 3}
            if 0 in bin_mean and len(bin_mean) >= 3:
                base_anom = bin_mean[0]
                curve = {b: round(m - base_anom, 1)
                         for b, m in sorted(bin_mean.items()) if b > 0}
                bin_n = {b: len(v) for b, v in bins.items()}
                # low-CDD slope for the latent line; guard against a noisy
                # or negative first bin by using the best-fit through the
                # first two populated bins forced through the origin
                lows = [(b - 0.5, curve[b]) for b in sorted(curve)[:2]]
                num = sum(x * y for x, y in lows)
                den = sum(x * x for x, y in lows)
                slope_l = num / den if den else 0.0
                slope_ok = slope_l > 0
                # CEILING FIX, 11 Aug 2026, and the fit is computed HERE -
                # above the weekly loop - so the weekly headline and the daily
                # series use ONE estimator. Until 17 Aug 2026 the fit sat
                # thirty lines below and the weekly loop still read the binned
                # curve, so the same metric had two implementations: the daily
                # chart was continuous and the weekly bar was the old shelf.
                # Found in peer review.
                #
                # The binned curve tops out at a shelf: the old top bin held
                # every day from CDD 4 to CDD 8 at one value, so 15 days a year
                # were served the same figure whatever the weather. Fitted
                # continuously instead - the same quadratic form the undemeaned
                # regression independently arrives at, so one shape is used
                # throughout. Also discards the negative low bin, which was
                # noise, and that is stated rather than hidden.
                num = den = 0.0
                mids = {1: 0.5, 2: 1.5, 3: 2.5, 4: 3.5, 5: 5.5}
                for b_, v_ in curve.items():
                    x_ = mids.get(int(b_), float(b_))
                    num += x_ * x_ * v_
                    den += x_ ** 4
                k_cool = (num / den) if den else 0.0

                wk_deliv = 0.0
                wk_latent = 0.0
                for d_ in wk:
                    c = dd["cdd"][COOL_BASE][dd_idx[d_]]
                    # SAME continuous quadratic as the daily series below.
                    wk_deliv += max(0.0, k_cool * c * c)
                    wk_latent += slope_l * c if slope_ok else 0.0
                # Daily tier 1 and 2 across the whole record, so the three
                # tiers can be drawn as a stacked column per day rather than
                # as one week's snapshot. Electricity GWh; the panel converts
                # to thermal with the same EER the tier bars use.
                # Spans the CURVE's own day set - NESO demand intersected with
                # degree days - not the gas window. The curve reaches back two
                # summers; clipping the series to gas would have thrown away
                # May and June 2025 and broken the 24-month view.
                daily_t12 = {"dates": [], "delivered_GWh": [], "unmet_GWh": [],
                             "k_quadratic": round(k_cool, 4)}
                for d_ in sorted(cdd_by_date):
                    c = cdd_by_date[d_]
                    b = 0 if c == 0 else min(5, int(c) + 1)
                    dv = k_cool * c * c        # continuous, no shelf
                    lt = slope_l * c if slope_ok else 0.0
                    daily_t12["dates"].append(d_)
                    daily_t12["delivered_GWh"].append(round(dv, 2))
                    daily_t12["unmet_GWh"].append(round(max(0.0, lt - dv), 2))
                cooling_observed = {
                    "daily": daily_t12,
                    "response_curve_GWh_per_day": curve,
                    "bin_days": {str(b): bin_n.get(b, 0)
                                 for b in sorted(bin_mean)},
                    "latent_slope_GWh_per_CDD": round(slope_l, 1),
                    "latent_slope_reliable": slope_ok,
                    "week_delivered_GWh": round(wk_deliv, 0),
                    "week_latent_GWh": round(max(wk_latent, wk_deliv), 0),
                    # kept as a diagnostic after the tier was removed: it is
                    # structurally zero on a convex response, and a non-zero
                    # value here would mean the curve had turned concave -
                    # which is the evidence that would bring the tier back
                    "week_unmet_GWh": round(max(0.0, wk_latent - wk_deliv), 0)
                        if slope_ok else None,
                    "summer_days_used": len(summer),
                    "note": ("Observed cooling electricity from summer daily "
                             "underlying demand (NESO ND + embedded solar/"
                             "wind reconstructed), centred within month and "
                             "weekend class to remove the holiday/seasonal "
                             "baseline, then fitted continuously against "
                             "cooling degree days. The response is CONVEX - "
                             "about +3 GWh per CDD across the cool bins "
                             "against +17 across the warm ones - and the bend "
                             "is LOAD, not plant: dividing out the falling "
                             "COP leaves it almost intact. It is the "
                             "extensive margin, each building a step and the "
                             "fleet the integral of those steps. Tested "
                             "against moist-air enthalpy days on the full "
                             "800-day record with embedded solar carried as a "
                             "control: dewpoint adds 0.0006 to R2 and enthalpy "
                             "days add 0.0000, against 0.031 for bank "
                             "holidays, and the quadratic term moves only 3% "
                             "when they enter. Humidity is a null and the bend "
                             "is the extensive margin. The test could have "
                             "failed: two earlier mis-specified runs moved the "
                             "quadratic by 25 and 27 points in opposite "
                             "directions, and it took three attempts - after a "
                             "double-counted solar term, an omitted solar "
                             "control, and a half-record fetch - to obtain a "
                             "null worth having (17 Aug 2026). "
                             "evidence. The centring "
                             "removes the LEVEL along with the confound, so "
                             "this recovers about a THIRD of the ECUK "
                             "anchor and is a lower bound by construction; "
                             "modelling the baseline explicitly on undemeaned "
                             "daily data recovers "
                             # Concatenated, not %-formatted: an earlier
                             # literal in this same implicit join contains a
                             # bare "3% ", and Python joins the literals
                             # BEFORE applying %, so a format operation here
                             # would try to read that as a conversion and
                             # fail at runtime.
                             + str(UNDEMEANED_FIT[
                                 "share_of_ecuk_anchor_pct"]) + "%. "
                             "A third tier, "
                             "equipped-but-saturated, was removed on 11 Aug "
                             "2026: its premise was that the fleet flattens "
                             "at high load, and the measured response "
                             "steepens instead. Not yet used in the bill or "
                             "carbon figures, which remain ECUK-anchored "
                             "pending a full summer of reconciliation."),
                }
    except Exception:
        traceback.print_exc()
        cooling_observed = prev.get("cooling_observed")
        out["sources"]["electricity"] = {
            "status": "stale" if cooling_observed else "unavailable",
            "last_good": prev.get("sources", {}).get("electricity", {}).get("last_good")}

    # --- cooling reconciliation DIAGNOSTIC: year-round joint regression -------
    # The observed summer curve (above) and the ECUK cooling+ventilation
    # anchor differ by an order of magnitude (methodology 4.5). This layer
    # runs the planned resolution as a DIAGNOSTIC ONLY - it does not drive
    # the bill or carbon panels. Daily underlying electricity demand over
    # the full record is regressed jointly on HDD, CDD (base swept - the
    # data chooses where conditioning starts), weekend, August and
    # Christmas-period dummies, and a linear trend. beta_c x trailing-12m
    # CDD bounds annual WEATHER-DRIVEN cooling electricity; the remainder
    # against the ECUK anchor is weather-flat (ventilation + base-load
    # cooling + below-base conditioning), which this method cannot split †.
    cooling_recon = None
    try:
        if elec and len(elec) >= 300:
            # daily mean temperature reconstructed from the dd feed: from
            # the highest HDD base when heating-side, from CDD(18) when
            # cooling-side, midpoint in the dead band. (If the dd module
            # ever exposes raw temps, swap them in here.)
            cr_bmax = max(dd["hdd"], key=float)
            cr_temp_by_date = {}
            for i, d_ in enumerate(dd["dates"]):
                h = dd["hdd"][cr_bmax][i]
                c = dd["cdd"][COOL_BASE][i]
                if h > 0:
                    t_ = float(cr_bmax) - h
                elif c > 0:
                    t_ = float(COOL_BASE) + c
                else:
                    t_ = (float(cr_bmax) + float(COOL_BASE)) / 2.0
                cr_temp_by_date[d_] = t_
            cr_days_r = sorted(set(elec) & set(cr_temp_by_date))
            if len(cr_days_r) >= 300:
                def _ols(cr_y, cr_cols):
                    """Pure-python OLS: returns coeffs, r2, resid_se, and
                    coefficient standard errors via normal equations."""
                    k = len(cr_cols); n = len(cr_y)
                    XtX = [[sum(cr_cols[a][i] * cr_cols[b][i] for i in range(n))
                            for b in range(k)] for a in range(k)]
                    Xty = [sum(cr_cols[a][i] * cr_y[i] for i in range(n))
                           for a in range(k)]
                    # solve XtX b = Xty and invert XtX (Gauss-Jordan)
                    M = [row[:] + [1.0 if a == b else 0.0
                                   for b in range(k)] + [Xty[a]]
                         for a, row in enumerate(XtX)]
                    for col in range(k):
                        piv = max(range(col, k), key=lambda r_: abs(M[r_][col]))
                        if abs(M[piv][col]) < 1e-9:
                            return None
                        M[col], M[piv] = M[piv], M[col]
                        pv = M[col][col]
                        M[col] = [v / pv for v in M[col]]
                        for r_ in range(k):
                            if r_ != col and M[r_][col]:
                                f = M[r_][col]
                                M[r_] = [v - f * w for v, w
                                         in zip(M[r_], M[col])]
                    beta = [M[a][-1] for a in range(k)]
                    inv = [[M[a][k + b] for b in range(k)] for a in range(k)]
                    yhat = [sum(beta[a] * cr_cols[a][i] for a in range(k))
                            for i in range(n)]
                    rss = sum((cr_y[i] - yhat[i]) ** 2 for i in range(n))
                    ybar = sum(cr_y) / n
                    tss = sum((v - ybar) ** 2 for v in cr_y) or 1.0
                    s2 = rss / max(n - k, 1)
                    return {"beta": beta, "r2": 1.0 - rss / tss,
                            "se": math.sqrt(s2),
                            "bse": [math.sqrt(max(s2 * inv[a][a], 0.0))
                                    for a in range(k)]}
                cr_y = [elec[d_] for d_ in cr_days_r]
                cr_n_r = len(cr_days_r)
                cr_meta = []
                for d_ in cr_days_r:
                    dtd = dt.date.fromisoformat(d_)
                    cr_meta.append((cr_temp_by_date[d_], dtd.weekday(),
                                 dtd.month, (dtd.month, dtd.day)))
                cr_best = None
                for cr_bh in sorted(float(b) for b in dd["hdd"]):
                    for cr_bc in (10.0, 12.0, 14.0, 16.0, 18.0):
                        if cr_bc <= cr_bh:
                            continue
                        cr_cols = [
                            [1.0] * cr_n_r,
                            [max(0.0, cr_bh - t_) for t_, *_ in cr_meta],
                            [max(0.0, t_ - cr_bc) for t_, *_ in cr_meta],
                            [1.0 if wd == 5 else 0.0
                             for _, wd, *_ in cr_meta],
                            [1.0 if wd == 6 else 0.0
                             for _, wd, *_ in cr_meta],
                            [1.0 if mo == 8 else 0.0
                             for _, _, mo, _ in cr_meta],
                            [1.0 if (md >= (12, 20) or md <= (1, 3)) else 0.0
                             for *_, md in cr_meta],
                            [i / cr_n_r for i in range(cr_n_r)],
                        ]
                        cr_fit = _ols(cr_y, cr_cols)
                        if cr_fit and (cr_best is None or cr_fit["r2"] > cr_best[0]["r2"]):
                            cr_best = (cr_fit, cr_bh, cr_bc)
                if cr_best:
                    cr_fit, cr_bh, cr_bc = cr_best
                    cr_beta_c, cr_bse_c = cr_fit["beta"][2], cr_fit["bse"][2]
                    cr_t_c = cr_beta_c / cr_bse_c if cr_bse_c else 0.0
                    cr_reliable = cr_beta_c > 0 and cr_t_c > 3 and cr_n_r >= 400
                    # trailing-12m and current-week CDD at the CHOSEN base
                    cr_last365 = cr_days_r[-365:]
                    cr_cdd12m = sum(max(0.0, cr_temp_by_date[d_] - cr_bc)
                                 for d_ in cr_last365)
                    cr_wk7 = [d_ for d_ in wk if d_ in cr_temp_by_date]
                    cr_cddwk = sum(max(0.0, cr_temp_by_date[d_] - cr_bc)
                                for d_ in cr_wk7)
                    cr_annual_gwh = cr_beta_c * cr_cdd12m
                    cr_week_gwh = cr_beta_c * cr_cddwk
                    cr_ecuk_annual = (ANNUAL_TWH["cooling_vent"]
                                   * GB_SHARE_OF_UK_GAS_HEAT * 1000.0)
                    cr_flat_share = (max(0.0, min(1.0, 1.0 - cr_annual_gwh
                                               / cr_ecuk_annual))
                                  if cr_ecuk_annual else None)
                    cooling_recon = {
                        "status": "diagnostic - not in bill/carbon",
                        "n_days": cr_n_r,
                        "window": {"from": cr_days_r[0], "to": cr_days_r[-1]},
                        "hdd_base_c": cr_bh, "cdd_base_c": cr_bc,
                        "beta_cool_GWh_per_CDD": round(cr_beta_c, 1),
                        "beta_cool_t_stat": round(cr_t_c, 1),
                        "beta_heat_GWh_per_HDD": round(cr_fit["beta"][1], 1),
                        "r2": round(cr_fit["r2"], 3),
                        "resid_se_GWh": round(cr_fit["se"], 0),
                        "reliable": cr_reliable,
                        "annual_weather_cooling_elec_GWh":
                            round(cr_annual_gwh, 0),
                        "week_weather_cooling_elec_GWh": round(cr_week_gwh, 0),
                        "ecuk_annual_cooling_vent_GWh": round(cr_ecuk_annual, 0),
                        "implied_weather_flat_share":
                            round(cr_flat_share, 2) if cr_flat_share is not None
                            else None,
                        "summer_centring_slope_GWh_per_CDD":
                            (cooling_observed or {}).get(
                                "latent_slope_GWh_per_CDD"),
                        "note": ("Diagnostic year-round OLS of daily "
                                 "underlying electricity demand on HDD + "
                                 "CDD jointly (CDD base swept 10-18 degC, "
                                 "chosen on fit - the data decides where "
                                 "conditioning starts), weekend, August "
                                 "and Christmas dummies, linear trend. "
                                 "beta_c x trailing-12m CDD bounds annual "
                                 "weather-driven cooling electricity; the "
                                 "remainder against the ECUK cooling & "
                                 "ventilation anchor is weather-flat "
                                 "(ventilation + base-load cooling + "
                                 "below-base conditioning), which this "
                                 "method cannot separate" + EST + ". Not "
                                 "yet used in the bill or carbon figures."),
                    }
                    # --- stability log: one entry per run-date, so the
                    # switchover gate (base stable +-1C over 60d, slope
                    # t>5, estimator agreement) is measurable, not
                    # eyeballed. Idempotent by date; capped; carried
                    # forward with the rest of the dict on error.
                    prev_log = ((prev.get("cooling_reconciliation") or {})
                                .get("stability_log") or [])
                    log = {e["date"]: e for e in prev_log
                           if isinstance(e, dict) and e.get("date")}
                    log[dt.date.today().isoformat()] = {
                        "date": dt.date.today().isoformat(),
                        "cdd_base_c": cr_bc,
                        "beta_cool": round(cr_beta_c, 2),
                        "t_stat": round(cr_t_c, 1),
                        "r2": round(cr_fit["r2"], 3),
                        "n_days": cr_n_r,
                        "centring_slope": (cooling_observed or {}).get(
                            "latent_slope_GWh_per_CDD"),
                    }
                    entries = [log[k] for k in sorted(log)][-120:]
                    cooling_recon["stability_log"] = entries
                    cutoff = (dt.date.today()
                              - dt.timedelta(days=60)).isoformat()
                    recent = [e for e in entries if e["date"] >= cutoff]
                    bases = [e["cdd_base_c"] for e in recent]
                    slopes = [e["beta_cool"] for e in recent]
                    cooling_recon["stability"] = {
                        "runs_60d": len(recent),
                        "base_range_60d": [min(bases), max(bases)],
                        "slope_range_60d": [min(slopes), max(slopes)],
                        "base_stable_pm1c": (max(bases) - min(bases)) <= 2.0,
                        "gate_note": ("Switchover gate: base stable within "
                                      "+-1C over 60d of runs, slope t>5, "
                                      "year-round and centring slopes "
                                      "within ~1.5x, holdout error "
                                      "comparable to in-sample."),
                    }
    except Exception:
        traceback.print_exc()
        cooling_recon = prev.get("cooling_reconciliation")
    if cooling_recon:
        out["cooling_reconciliation"] = cooling_recon


    # --- tier 3: the comfort deficit (latent cooling in unequipped stock) ------
    # The observed curve above only sees buildings that HAVE cooling. This
    # tier estimates the sweltering remainder. Anchors (sourced): <5% of UK
    # homes have AC (CCC; NESO ~3%); EHS 2022-23 ~11% of households report
    # overheating (low case); CCC "over half at risk" (high case); ~90% of
    # England's hospital buildings vulnerable to overheating (UKHACC); ONS
    # 3,271 excess deaths in the 2022 heat-periods; ONS hot-day productivity
    # loss ~GBP 1.2bn/yr average (GBP 5.3bn peak 2020).
    # Judgement constants (all †): central overheating fraction, per-dwelling
    # and per-m2 thermal response per degree-hour, uncooled non-domestic area.
    UK_DWELLINGS_M = 29.9          # MHCLG-derived UK total, millions
    AC_PENETRATION = 0.05          # CCC/NESO: <5% of homes
    F_OVERHEAT = {"low": 0.11, "central": 0.25, "high": 0.50}  # EHS / † / CCC
    KWH_PER_DWELLING_ODH = 0.2     # † kWh thermal per degC.h per dwelling
                                   #   (~UA 200 W/K effective cooled zone)
    NONDOM_UNCOOLED_MM2 = 190.0    # † Mm2 comfort space uncooled: education
                                   #   ~72 + health ~45 + share of offices/
                                   #   other (BEES floor areas x † fractions)
    WH_PER_M2_ODH = 3.0            # † Wh thermal per m2 per degC.h
    GROUND_COOL_COP = 20.0         # passive/free ground cooling †
    # Two different efficiencies, for two different questions. COOL_EER (3.0,
    # module constant) is the INSTALLED FLEET - old and new kit, well and
    # badly maintained - and is what the rest of the site converts delivered
    # cooling at. AIR_COOL_EER is what a modern air-source unit would deliver,
    # and belongs only in the counterfactual: what serving the DEFICIT would
    # cost if it were met by new compressors. Corrected 10 Aug 2026 - tiers 1
    # and 2 had been converting the installed fleet at 3.5, which put them
    # about 17% above the same quantity in the energy bars.
    AIR_COOL_EER = 3.5             # new air-source kit, counterfactual only

    comfort_deficit = None
    try:
        # Degree-hours from the site's own daily-mean cooling degree days -
        # the same temperature spine every other panel uses - rather than a
        # separate hourly threshold count. One basis, one series.
        base_used = DEFICIT_BASE
        # Prefer the published series, but derive it from the daily means if
        # the base is not in CDD_BASES - so trimming that list cannot silently
        # remove a panel, and the base and its reasoning live in one place.
        #
        # ARCHIVE FIRST, LIVE FETCH OVER IT - the same precedence build_history
        # uses. The archive is the only thing that reaches back five years; the
        # live fetch is fresher and is authoritative wherever it covers a day.
        # Added 20 Nov 2026 so panel 4 can draw the 48- and 60-month windows.
        #
        # The two sources are NOT interchangeable to the last digit, and that
        # is worth stating rather than smoothing over. fetch_degree_days
        # computes CDD from the UNROUNDED daily mean and publishes the mean
        # rounded to 2 dp; the archive stores only the rounded mean, so a
        # degree day derived from it can differ by up to 0.005 C. So the
        # published series is used wherever it exists and the archive fills
        # only the days before it - every figure the page states today is
        # unchanged, and the extension is strictly backwards.
        def _temp_spine():
            t = {}
            if archive and archive.get("dates"):
                for d_, v_ in zip(archive["dates"],
                                  archive.get("gb_mean_temp") or []):
                    if v_ is not None:
                        t[d_] = v_
            t.update({d_: v_ for d_, v_
                      in zip(dd["dates"], dd["gb_mean_temp"])
                      if v_ is not None})
            return t

        def _cdd_spine(base):
            """CDD on `base` over archive + live, published values preferred."""
            b = float(base)
            out_ = {d_: round(max(0.0, t_ - b), 2)
                    for d_, t_ in _temp_spine().items()}
            if str(base) in dd["cdd"]:
                out_.update(zip(dd["dates"], dd["cdd"][str(base)]))
            return out_

        cdd_def = _cdd_spine(base_used)
        if base_used not in dd["cdd"]:
            print("comfort deficit: base %s not in CDD_BASES, derived from "
                  "daily means" % base_used)
        print("comfort deficit: temperature spine %d days %s to %s"
              % (len(cdd_def), min(cdd_def), max(cdd_def)))
        def_days = sorted(cdd_def)[-7:]
        odh_week = round(sum(cdd_def[d_] for d_ in def_days) * 24.0, 1)
        out["sources"]["overheating"] = {
            "status": "ok",
            "last_good": def_days[-1] if def_days else None,
            "base_c": float(base_used)}
        scen = {}
        for name, f in F_OVERHEAT.items():
            n_dw = UK_DWELLINGS_M * 1e6 * f * (1 - AC_PENETRATION)
            dom_gwh = n_dw * KWH_PER_DWELLING_ODH * odh_week / 1e6
            nd_gwh = (NONDOM_UNCOOLED_MM2 * 1e6 * WH_PER_M2_ODH
                      * odh_week / 1e9)
            scen[name] = {
                "dwellings_M": round(n_dw / 1e6, 1),
                "latent_thermal_GWh": round(dom_gwh + nd_gwh, 0),
            }
        # Tier 3 per day on the same base, so all three tiers can be drawn
        # as one stacked column per day. Thermal GWh, central scenario.
        f_c = F_OVERHEAT["central"]
        n_dw_c = UK_DWELLINGS_M * 1e6 * f_c * (1 - AC_PENETRATION)
        per_degCh = (n_dw_c * KWH_PER_DWELLING_ODH / 1e6
                     + NONDOM_UNCOOLED_MM2 * 1e6 * WH_PER_M2_ODH / 1e9)
        t12 = (cooling_observed or {}).get("daily") or {}
        idx12 = {d_: i for i, d_ in enumerate(t12.get("dates", []))}
        # TIER 2 REMOVED 11 Aug 2026, and the old tier 3 renumbered into its
        # place. The removed tier was "equipped but saturated" = latent minus
        # delivered, where latent extrapolated the LOW-CDD slope linearly. Its
        # premise was that the fleet flattens at high load. The measured
        # response does the opposite - it STEEPENS, about +3 GWh per CDD across
        # the cool bins against +17 across the warm ones - so the linear
        # extrapolation sits below the observed curve at high CDD and the tier
        # was structurally zero. Saturation would show as a concave curve; this
        # one is convex. The band was measuring a phenomenon this data says
        # does not happen.
        # Daily population-weighted mean temperature, already published by
        # fetch_degree_days - the same series the degree days are derived from,
        # so the two estimates are evaluated on identical weather.
        tmean_by_date = _temp_spine()
        # CDD on the COOLING base, not the deficit base. Tier 1's quadratic was
        # fitted against CDD(COOL_BASE); evaluating it on CDD(DEFICIT_BASE)
        # would be a different variable wearing the same name.
        cdd_cool = _cdd_spine(COOL_BASE)
        k_q = t12.get("k_quadratic")
        tier_daily = {"dates": [], "t1_GWh": [], "t2_GWh": [], "t1_alt_GWh": []}
        # NO 735-DAY CAP since 20 Nov 2026. It used to be WINDOW_DAYS + 5, and
        # WINDOW_DAYS is the REGRESSION SAMPLE - it was never meant to bound a
        # drawn series. The span is now whatever the spine covers, and the page
        # prunes its own window buttons to what the data reaches.
        t1_fitted = 0
        for d_ in sorted(cdd_def):
            i = idx12.get(d_)
            tier_daily["dates"].append(d_)
            # TIER 1 IS A FITTED VALUE ON EVERY DAY, not a measurement on some
            # of them - cooling_observed's delivered_GWh is k_quadratic x CDD^2,
            # the continuous fit, for 2026 exactly as much as for 2022. The
            # comment here used to call it "measured electricity multiplied up
            # by the fleet EER", which described the regression's INPUT, not
            # this series. So extending it back over the archive asserts
            # nothing new: it is the same function on more days. What IS
            # window-bound is the fit itself - k_quadratic comes from the three
            # calendar years of NESO demand the regression samples - so the
            # archive-era bars are that fit extrapolated onto older weather,
            # which is the same standing as t1_alt_GWh, and the panel says so.
            if i is not None:
                tier_daily["t1_GWh"].append(
                    round(t12["delivered_GWh"][i] * COOL_EER, 2))
            elif k_q is not None and d_ in cdd_cool:
                tier_daily["t1_GWh"].append(
                    round(k_q * cdd_cool[d_] ** 2 * COOL_EER, 2))
                t1_fitted += 1
            else:
                tier_daily["t1_GWh"].append(None)
            # tier 2 is the former tier 3: buildings with no cooling at all.
            # A thermal load computed directly from building physics - it
            # never touches an efficiency, unlike tier 1 which carries the
            # fleet EER.
            tier_daily["t2_GWh"].append(
                round(cdd_def[d_] * 24.0 * per_degCh, 2))
            # SECOND ESTIMATE, same day, independent method. The undemeaned
            # regression's cooling limb, evaluated on that day's mean
            # temperature and converted to thermal on the same EER the Tier 1
            # bars use, so the two are directly comparable.
            tmean = tmean_by_date.get(d_)
            tier_daily["t1_alt_GWh"].append(
                round(UNDEMEANED_FIT["k_elec_GWh_per_degC2"]
                      * max(0.0, tmean - UNDEMEANED_FIT["balance_point_C"]) ** 2
                      * COOL_EER, 2)
                if tmean is not None else None)

        # WHERE THE REGRESSION SAMPLE STARTS, published so the panel can mark
        # the extrapolated stretch instead of drawing one undifferentiated
        # series. Without this the 48- and 60-month views would show four or
        # five years of tier 1 and give no clue that the fit behind it was
        # sampled on the last three.
        _t1_in = sorted(d_ for d_ in tier_daily["dates"] if d_ in idx12)
        print("comfort deficit: tier series %d days %s to %s; tier 1 in-sample "
              "from %s, %d days extrapolated"
              % (len(tier_daily["dates"]), tier_daily["dates"][0],
                 tier_daily["dates"][-1], _t1_in[0] if _t1_in else "n/a",
                 t1_fitted))

        central = scen["central"]["latent_thermal_GWh"]
        comfort_deficit = {
            "cdh_week_degC_h": odh_week,
            "tier_daily": tier_daily,
            "tier_days": len(tier_daily["dates"]),
            "tier_from": tier_daily["dates"][0] if tier_daily["dates"] else None,
            "tier_to": tier_daily["dates"][-1] if tier_daily["dates"] else None,
            # Tier 1 is the fitted quadratic throughout; this is where the
            # days behind the fit begin. Before it, the same fit on weather it
            # never saw.
            "t1_in_sample_from": _t1_in[0] if _t1_in else None,
            "t1_extrapolated_days": t1_fitted,
            # The pin travels WITH the series it generated. Until 19 Nov 2026
            # the panel's headline figures were typed into index.html by hand,
            # so when the fit was re-derived on 22 Oct the page went on
            # publishing 4.9 TWh and 102% against a pin that said 4.56 and 95%
            # - for four weeks, with nothing able to notice. A number the page
            # states about a fit it does not read is a number that will drift.
            "undemeaned": dict(UNDEMEANED_FIT),
            "threshold_c": float(base_used),
            "basis": "cooling degree-hours on the daily mean of "
                     "population-weighted outdoor air, base %s C" % base_used,
            "scenarios": scen,
            "elec_if_ground_GWh": round(central / GROUND_COOL_COP, 1),
            "elec_if_air_GWh": round(central / AIR_COOL_EER, 1),
            "context": {
                "ac_penetration_note": "fewer than 5% of UK homes have air "
                    "conditioning (CCC; NESO ~3%)",
                "hospitals_note": "~90% of England's hospital buildings are "
                    "vulnerable to overheating (UKHACC); NHS overheating "
                    "incidents 5,554 in 2021-22",
                "health_note": "3,271 excess deaths in the England & Wales "
                    "2022 heat-periods (ONS/UKHSA)",
                "productivity_note": "hot days cost GB ~GBP 1.2bn/yr in lost "
                    "output on average, GBP 5.3bn in 2020 (ONS)",
            },
            "tier_bars": ({
                "t1_delivered_th_GWh": round(
                    cooling_observed["week_delivered_GWh"] * COOL_EER, 0),
                "t2_low_GWh": scen["low"]["latent_thermal_GWh"],
                "t2_central_GWh": scen["central"]["latent_thermal_GWh"],
                "t2_high_GWh": scen["high"]["latent_thermal_GWh"],
            } if cooling_observed else None),
            "utes": {
                # if the central tier-3 load were ground-served, the rejected
                # heat (load + pump input) banks in the store; UTES round-trip
                # thermal recovery ~70% (ATES literature range 50-80%) †
                "round_trip": 0.7,
                "summer_banked_GWh": round(
                    central * (1 + 1 / GROUND_COOL_COP), 0),
                "winter_recovered_GWh": round(
                    central * (1 + 1 / GROUND_COOL_COP) * 0.7, 0),
            },
            "note": ("NOTE this base is not the 15.5 degC cooling shape base used "
                     "elsewhere on the page - it answers a different question. "
                     "Cooling degree-hours on the daily mean of "
                     "population-weighted outdoor air above a " + base_used +
                     " degC base, x the unequipped stock at risk (low: EHS "
                     "11%; central 25%" + EST + "; high: CCC over-half-at-risk) "
                     "x a per-dwelling and per-m2 thermal response" + EST +
                     ". The base comes from the 2017 Energy Follow-Up Survey "
                     "indoor/outdoor regression, NOT from CIBSE's 26 degC, "
                     "which is an indoor criterion and had been misapplied to "
                     "outdoor air - that understated this tier about "
                     "five-fold. A night-time urban heat island uplift would "
                     "add 10-20% more and is not in the numbers" + EST + "."),
        }
    except Exception:
        traceback.print_exc()
        comfort_deficit = prev.get("comfort_deficit")
        out["sources"]["overheating"] = {
            "status": "stale" if comfort_deficit else "unavailable",
            "last_good": prev.get("sources", {}).get("overheating", {}).get("last_good")}

    # winter context for summer visitors
    peak_i = max(range(len(space_heat) - 6),
                 key=lambda i: sum(space_heat[i:i + 7]))
    peak_week = {
        "week_ending": common[peak_i + 6],
        "space_heat_GWh": round(sum(space_heat[peak_i:peak_i + 7]), 0),
    }

    out.update({
        "regression": best,
        "calibration": calibration,
        "weekly": weekly,
        "weekly_mix": weekly_mix,
        "weekly_useful": weekly_useful,
        "whatif_bars": whatif_bars,
        "geothermal": geothermal,
        "cost": cost,
        "headlines": headlines,
        "spark": spark,
        "ni_panel": ni_panel,
        "sibling": sibling,
        "why_heat": {
            # Annual, UK, calendar 2024. Sourced anchors: ECUK 2025 (final
            # energy 128.1 mtoe = ~1,490 TWh; transport 54.0 mtoe = 628 TWh,
            # 42%, 93% petroleum); DESNZ 2024 provisional GHG by sector
            # (transport the largest emitting sector); DUKES import
            # dependency; dashboard INDIG origin shares. Service-level
            # allocations are Causeway derivations - all daggered.
            "year": 2024,
            "services_TWh": {"heat": 630, "transport": 628,
                             "power_other": 235},
            "cost_bn": {"heat": 42, "transport": 70, "power_other": 60},
            "imported_TWh": {"heat": 233, "transport": 415,
                             "power_other": 59},
            "emissions_Mt": {"heat": 125, "transport": 115,
                             "power_other": 30},
            "note": ("Annual, calendar 2024. Sourced: UK final energy "
                     "consumption 1,490 TWh and transport 628 TWh (42%, 93% "
                     "petroleum) - DESNZ ECUK 2025; transport the largest "
                     "emitting sector - DESNZ 2024 provisional. Derived" +
                     EST + ": heat = heat end uses across homes, services "
                     "and industry (~630 TWh - the largest single use of "
                     "energy in Britain); power = non-heat electricity; "
                     "costs from typical 2024 retail/pump prices x volumes; "
                     "imports allocate each service's inputs by the origin "
                     "shares used site-wide (gas ~42% UK, electricity ~75%, "
                     "petroleum ~30%); emissions allocate combustion by "
                     "service and grid CO2 by electricity use. Non-energy "
                     "emissions (agriculture, waste, F-gases) excluded. "
                     "The pattern the four pies show: heat is the biggest "
                     "energy service, the cheapest per unit (untaxed "
                     "fossil fuel), a major import driver, and a top-tier "
                     "emitter - which is why it is the transition's "
                     "biggest prize."),
        },
        "carbon": carbon,
        "cooling_observed": cooling_observed,
        "comfort_deficit": comfort_deficit,
        "peak_week": peak_week,
        "series": {
            "dates": common,
            "gas_GWh": [round(v, 1) for v in y],
            "nts_GWh": [nts.get(d) for d in common],
            "hdd": hdd_series,
            "cdd": cdd_series,
            "space_heat_GWh": space_heat,
        },
        "ni_note": {
            "hdd_15_5_latest": dd["ni"]["hdd_15_5"][-1] if dd["ni"]["hdd_15_5"] else None,
            "note": ("NI is not on the GB NTS/LDZ feed. NI heating is "
                     "estimated annually from subnational consumption "
                     "statistics, scaled by NI degree days."),
        },
        "_dd": dd,
        "_gas": gas,
    })

    print("regression:", best)
    print("calibration:", calibration)
    print("weekly:", weekly)
    print("weekly_mix:", weekly_mix["components_GWh"],
          "total", weekly_mix["total_GWh"],
          "combustion", weekly_mix["combustion_share"])
    print("weekly_useful:", weekly_useful["components_GWh"],
          "total", weekly_useful["total_GWh"],
          "wasted", weekly_useful["wasted_GWh"])
    print("geothermal:", geothermal)
    print("cost:", {k: cost[k] for k in ("gshp_vs_gas_boiler","national_week_total_Mgbp")})
    print("headlines:", headlines)
    print("spark:", spark)
    print("ni_panel:", ni_panel)
    if cooling_recon:
        print("cooling_recon: base", cooling_recon.get("cdd_base_c"),
              "slope", cooling_recon.get("beta_cool_GWh_per_CDD"),
              "GWh/CDD t", cooling_recon.get("beta_cool_t_stat"),
              "R2", cooling_recon.get("r2"),
              "| weather", cooling_recon.get(
                  "annual_weather_cooling_elec_GWh"),
              "GWh/yr vs ECUK", cooling_recon.get(
                  "ecuk_annual_cooling_vent_GWh"),
              "| flat share", cooling_recon.get(
                  "implied_weather_flat_share"),
              "| stability:", (cooling_recon.get("stability") or {}).get(
                  "runs_60d"), "runs, base",
              (cooling_recon.get("stability") or {}).get("base_range_60d"))
    if sibling:
        print("sibling: UK", sibling["uk"]["per_capita_MWh"],
              "vs island", sibling["island"]["per_capita_MWh"],
              "MWh/person, ratio", sibling["ratio_island_over_uk"],
              "within gate", sibling["within_gate"])
    print("carbon:", carbon)
    print("cooling_observed:", cooling_observed)
    print("comfort_deficit:", comfort_deficit)
    print("peak week:", peak_week)
    print("gas_window:", out.get("gas_window"))
    hist = out.get("history") or []
    print(f"history: {len(hist)} weeks",
          f"({hist[0]['week_ending']} .. {hist[-1]['week_ending']})"
          if hist else "")
    # ---- v7 Phase B: hourly retrospective + slices (gated) --------------
    try:
        useful_space = (annual_space_twh * EFF["gas"]
                        + ANNUAL_TWH["oil_space"] * EFF["oil"]
                        + ANNUAL_TWH["bio_space"] * EFF["bio"]
                        + ANNUAL_TWH["solid"] * EFF["solid"]
                        + ANNUAL_TWH["heat_networks"] * EFF["heat_networks"]
                        + max(0.0, ANNUAL_TWH["elec_space"] - HP_ELEC_TWH)
                        * EFF["resistive"]
                        + HP_ELEC_TWH * HP_SPF)
        # NOTE this feeds the retro store's dhw_annual_TWh, and the sum of
        # space + dhw is the DENOMINATOR of the electrification limit. Moving
        # hot water onto the summer efficiency lowers useful DHW and therefore
        # raises the limit slightly.
        useful_dhw = (ANNUAL_TWH["gas_dhw"] * EFF_DHW["gas"]
                      + ANNUAL_TWH["oil_dhw"] * EFF_DHW["oil"]
                      + ANNUAL_TWH["bio_dhw"] * EFF["bio"]
                      + ANNUAL_TWH["elec_dhw"] * EFF["resistive"])
        g2_weekly_twh = (useful_space + useful_dhw) * 0.20 / 5.0
        r_end = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        prev_retro = retro.load()
        if not prev_retro:
            r_start = (dt.date.today()
                       - dt.timedelta(days=2 + RETRO_SPAN_DAYS)).isoformat()
        elif RETRO_BACKFILL:
            # STEP 2, deliberate and one-off. Moving the start breaks the
            # start_day match in build_retro, so the WHOLE span is refetched
            # from four feeds rather than extended by two days. Expect a long
            # run.
            #
            # WHICH FIGURES MOVE is set by retro.py's search windows, not by
            # the span: see the note at RETRO_SPAN_DAYS. The sentence that
            # stood here until 20 Nov 2026 claimed the binding hour, headroom
            # and electrification limit would all follow a colder hour. They
            # do not - _elec_limits searches the trailing year by design. The
            # worst-hour cards move if, and only if, WORST_HOUR_ALL_WINTERS
            # is on in retro.py.
            want = (dt.date.today()
                    - dt.timedelta(days=2 + RETRO_SPAN_DAYS)).isoformat()
            r_start = min(want, prev_retro["start_day"])
            if r_start != prev_retro["start_day"]:
                print("retro BACKFILL: start %s -> %s (full refetch of %d days)"
                      % (prev_retro["start_day"], r_start,
                         (dt.date.fromisoformat(r_end)
                          - dt.date.fromisoformat(r_start)).days + 1))
        else:
            r_start = prev_retro["start_day"]
        rstore = retro.build_retro(
            r_start, r_end, useful_space, useful_dhw,
            resistive_space_twh=max(
                0.0, ANNUAL_TWH["elec_space"] - HP_ELEC_TWH),
            base_c=float(best["base_temp"]),
            cooling={"annual_gwh": ANNUAL_TWH["cooling_vent"]
                     * GB_SHARE_OF_UK_GAS_HEAT * 1000.0,
                     "base_c": float(COOL_BASE),
                     "eer": COOL_EER,
                     "passive_cop": PASSIVE_COOL_COP},
            prev=prev_retro, **RETRO_FETCHERS)
        rstore = retro.trim(rstore)
        r_ok, r_rep = retro.gates(rstore, g2_weekly_twh)
        print("retro gates:", "PASS" if r_ok else "FAIL",
              {k: v for k, v in r_rep.items()})
        print("retro eta:", rstore["calibration"]["eta"],
              "| spf:", rstore["calibration"]["spf_check"])
        if not r_ok:
            raise RuntimeError("retro gates failed")
        retro.save(rstore)

        # APPEND-ONLY DAILY ARCHIVE. retro.json rolls; this does not. Every
        # run adds the days the rolling store just saw, so the regression
        # record keeps growing after the hourly window has moved on. Cheap -
        # about 40 bytes a day - and it is what makes a pinned coefficient
        # reproducible after the fact.
        #
        # Deliberately non-fatal: a failure here must not take the site build
        # down, because nothing on the published page depends on the archive.
        # It is read by analysis/fit_cooling.py, offline, between runs.
        try:
            import daily_archive as _da
            _arch = _da.load() or _da.empty()
            _rows = _da.seed_from_retro(rstore)
            # seed_from_retro covers the retro fields, which from schema 2
            # includes ci_g_kwh and mid_gbp_mwh. The other three live only in
            # THIS function's fetches, so without attaching them here a new day
            # would be stored with gas and the degree-day temperature null and
            # the loss would simply resume for three fields out of five.
            _tmap_a = dict(zip(dd["dates"], dd["gb_mean_temp"]))
            for _r in _rows:
                _k = _r["d"].isoformat()
                if _tmap_a.get(_k) is not None:
                    _r["gb_mean_temp"] = _tmap_a[_k]
                if target.get(_k) is not None:
                    _r["gas_GWh"] = target[_k]
                if nts.get(_k) is not None:
                    _r["nts_GWh"] = nts[_k]
            if _arch["dates"]:
                _last = dt.date.fromisoformat(_arch["dates"][-1])
                _rows = [r for r in _rows
                         if r["d"] > _last - dt.timedelta(days=_da.REVISE_DAYS)]
            if _rows:
                _arch = _da.upsert(_arch, _rows)
                _da.save(_arch)
            # TOP-UP PASS. upsert only touches the trailing revision window, so
            # it cannot fill a field on an older day. This offers every field
            # this run holds across the whole archive, and fill_nulls writes
            # only where the value is null - so it is a no-op on a settled
            # archive and self-healing when a feed widens. The gas feed is the
            # live case: its span is set by the days we request, so if that
            # request ever reaches further back, those days fill themselves
            # here instead of waiting for someone to run --enrich.
            _topup = False
            for _f, _src in (("gb_mean_temp", _tmap_a),
                             ("gas_GWh", target),
                             ("nts_GWh", nts)):
                if _da.fill_nulls(_arch, _f, _src, "this run")["filled"]:
                    _topup = True
            if _topup:
                _da.save(_arch)
            print("daily archive:", _da.describe(_arch))
            for _c in check_undemeaned_pin(_arch["dates"]):
                print("WARNING:", _c)
        except Exception as _e:                              # noqa: BLE001
            print("daily archive: SKIPPED (%s: %s)"
                  % (type(_e).__name__, _e))
        nd_daily = None
        try:
            nd_daily = elec if isinstance(elec, dict) else None
        except NameError:
            pass
        # SPAN AND BEFORE/AFTER. A changed span moves the headlines for a
        # legitimate reason, but only if you can see WHICH change moved them.
        # The previous store's own figures are printed alongside the new ones
        # so a shift is attributable rather than merely noticed.
        _pv = (prev_retro or {})
        _pspan = len(_pv.get("temp_C") or []) // 24
        _nspan = rstore["n_hours"] // 24
        # cap printed with %s, not %d: MAX_DAYS is None from 20 Nov 2026 and
        # %d against None raises, which would have failed the whole build on
        # the next run.
        print("retro span: %d -> %d days (%s -> %s), cap %s%s"
              % (_pspan, _nspan, _pv.get("start_day", "-"),
                 rstore["start_day"],
                 "none (retains all)" if retro.MAX_DAYS is None
                 else str(retro.MAX_DAYS),
                 "  BACKFILL RUN" if RETRO_BACKFILL else ""))
        if _pspan and _pv.get("demand_GW"):
            try:
                _before = retro.slices(_pv)
                _bs, _ns = _before["stress"], None
                print("  before: worst %s at %s C, %s GWh"
                      % (_bs["worst_hour"], _bs["worst_hour_temp_C"],
                         _bs["worst_hour_heat_GWh"]))
                _bsv = _before.get("system")
                if _bsv:
                    _bl = _bsv["limits"]["routes"]
                    print("  before: limits %s"
                          % {r: _bl[r]["share_pct"]
                             for r in ("ashp", "shallow", "network")})
            except Exception as _e:                  # diagnostic only
                print("  before: not comparable (%s)" % type(_e).__name__)

        # The gas line on the heat-price chart needs a SERIES, not today's
        # single SAP. Fetched here and handed to the retro slice so the four
        # routes are drawn on one basis from one place.
        # RETAIL BASIS for the price chart. The cap in force on each date,
        # per useful kWh, so a reader can switch between "which fuel is
        # cheaper to make heat from" and "what would I pay". A step function
        # by construction - cap periods, not a market.
        _retail_series = None
        try:
            _rd = (rstore or {}).get("start_day")
            if _rd:
                _n = (rstore["n_hours"] // 24)
                _dates = [(dt.date.fromisoformat(_rd) + dt.timedelta(days=i))
                          .isoformat() for i in range(_n)]
                _retail_series = {"dates": _dates, "gas": [], "ashp": [],
                                  "shallow": [], "network": []}
                # WHERE THE RETAIL BASIS STOPS BEING THAT QUARTER'S CAP. On a
                # 48- or 60-month window this matters more than it does at
                # 24: before 2024 cap_prices resolves to a QEP ANNUAL MEAN,
                # so the energy crisis renders as three flat steps on the
                # retail basis while the wholesale basis shows it at full
                # amplitude. Both are correct and they look nothing alike;
                # a reader toggling between them deserves to be told why.
                # Panel 1 already publishes this for its history weeks.
                _annual_days = []
                for _d in _dates:
                    _p, _m = cap_prices(_d)
                    if isinstance(_m, dict) and _m.get("_annual"):
                        _annual_days.append(_d)
                    _retail_series["gas"].append(
                        round(_p["gas"] / EFF["gas"] * 10.0, 2))
                    _retail_series["ashp"].append(
                        round(_p["elec"] * 10.0, 2))
                    _retail_series["shallow"].append(
                        round(_p["elec"] * 10.0, 2))
                    # A HEAT NETWORK OPERATOR IS NOT A HOUSEHOLD. It buys
                    # electricity on a commercial contract, not the domestic
                    # cap, so the network route is priced at the non-domestic
                    # rate. Using the cap here charged the operator a domestic
                    # tariff it would never pay.
                    _retail_series["network"].append(
                        round(NONDOM_PRICES_P_PER_KWH["elec"] * 10.0, 2))
                # Unit ELECTRICITY prices per MWh; the front end divides by
                # that day's COP so the retail and wholesale bases use one
                # efficiency model rather than two.
                _retail_series["basis"] = "unit_electricity"
                _retail_series["annual_mean_to"] = (
                    _annual_days[-1] if _annual_days else None)
                _retail_series["annual_mean_days"] = len(_annual_days)
        except Exception:
            traceback.print_exc()

        _sap_series = None
        try:
            _sap_series = fetch_gas_sap_series(days=RETRO_SPAN_DAYS)
        except Exception as _e:
            # A bare exception name hid a plain coding error for a whole run.
            # Print the traceback: a feed being down and a bug in the caller
            # look identical from the outside and need different fixes.
            print("gas SAP series unavailable - gas line will be absent")
            traceback.print_exc()
        out["whatif_routes"] = retro.slices(rstore, nd_daily=nd_daily,
                                            sap_series=_sap_series,
                                            retail_series=_retail_series)
        st = out["whatif_routes"]["stress"]
        print("retro stress: worst hour", st["worst_hour"],
              "heat", st["worst_hour_heat_GWh"], "GWh at",
              st["worst_hour_temp_C"], "C |",
              {r: st[r]["added_GW"] for r in ("ashp", "shallow",
                                              "network")}, "GW added")
        print("retro stress: scope %s, winters searched %s"
              % (st.get("worst_hour_scope"), st.get("winters_searched")))
        # A STALE CAPACITY TABLE IS NOW LOUD. Hours in a winter with no
        # published de-rated block are excluded from the limit rather than
        # priced at the latest winter's fleet. That is right for a winter
        # that is over and wrong-feeling for the one in progress: as
        # 2026/27 fills up, an unupdated table would quietly stop the limit
        # describing this winter at all. So it is printed as a WARNING, and
        # November-only skips are called out as the harmless case they are
        # while they last.
        _sv0 = out["whatif_routes"].get("system") or {}
        _skip = _sv0.get("winters_skipped_no_block") or []
        if _skip:
            print("WARNING: electrification limit EXCLUDES winter(s) %s - "
                  "no de-rated block in retro.DISPATCH_BY_WINTER. Limit is "
                  "solved over %s only. Add the NESO Winter Outlook figure "
                  "for each, derived on the SAME basis as the existing "
                  "entries (total de-rated capacity less de-rated wind); "
                  "note NESO changed methodology for 2026/27, so its "
                  "headline margin is not comparable with the earlier ones."
                  % (_skip, _sv0.get("winters_priced")))
        ss = out["whatif_routes"].get("stress_summer")
        if ss:
            print("retro summer: peak cool hour", ss["peak_hour"],
                  ss["today_GW"], "GW today |", ss["x2_scenario_added_GW"],
                  "GW if doubled |", ss["whatif_relief_GW"], "GW relief")
        _wint = sorted({d_[:7] for d_ in
                        [(dt.date.fromisoformat(rstore["start_day"])
                          + dt.timedelta(days=i)).isoformat()
                         for i in range(rstore["n_hours"] // 24)]
                        if d_[5:7] in ("12", "01", "02")})
        print("retro winters covered: %s" % (", ".join(_wint) or "none"))
        sv_ = out["whatif_routes"].get("system")
        if sv_ and sv_.get("limits", {}).get("routes"):
            _al = sv_["limits"]["routes"]
            print("  after:  limits %s"
                  % {r: _al[r]["share_pct"]
                     for r in ("ashp", "shallow", "network")})
        if sv_:
            ab = sv_["routes"]["ashp"]
            print("retro binding hour:", ab["binding_hour"],
                  "req", ab["dispatch_req_GW"], "GW vs block",
                  ab.get("block_GW"), "GW, headroom",
                  ab["headroom_GW"], "GW |",
                  {r: sv_["routes"][r]["dispatch_req_GW"]
                   for r in ("ashp", "shallow", "network")})
        # log-only since 9 Aug 2026: the panel was withdrawn, the
        # statistic was not - it still runs every day and prints here
        _prem = out["whatif_routes"].get("coincidence_premium_diagnostic")
        if _prem:
            print("retro premium (diagnostic, not published):",
                  {r: _prem[r]["premium_pct"] for r in ("ashp", "shallow",
                                                        "network")}, "%",
                  "|", _prem.get("window"))
    except Exception:
        traceback.print_exc()
        if prev.get("whatif_routes"):
            out["whatif_routes"] = prev["whatif_routes"]
            print("retro: carried previous whatif_routes")
        else:
            print("retro: unavailable, no previous to carry")
    _write(out)


def _write(out):
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT_PATH}")
    if "error" in out:
        print("ERROR:", out["error"])
        sys.exit(1)


if __name__ == "__main__":
    main()
