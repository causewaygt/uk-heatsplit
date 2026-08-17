# Handover: how UK Heat Split separates hot water from space heating

**For the Irish Heat Split build · Causeway Energies · 13 August 2026 ·
UK site v8.32.0**

This note explains one decision and everything that hangs off it. The
separation of domestic hot water from space heating looks like bookkeeping and
is not: it is what allows the UK site to claim that gas space heating is
*measured live* while everything else is *anchored and shaped*. Get it wrong
and the live claim collapses.

Read this alongside §2.4 and §3 of the UK methodology.

---

## 1. The principle

**Space heating responds to the weather. Hot water does not.**

A building's space-heating load is a function of the temperature difference
across its fabric. Its hot water load is a function of how many people take how
many showers, which is very nearly the same in January and July. Any estimator
that treats the two as one quantity will either smear a constant load across a
degree-day curve or attribute a weather response to something that has none.

Everything below follows from that one observation.

---

## 2. Where the split comes from

**Not from an assumption.** It comes from the national end-use tables, which
already report the two separately. On the UK site that is ECUK End Use tables
U3 (domestic) and U5 (services), calendar 2024, published 2025.

The UK constants, in TWh/yr, with their table provenance in comments:

```python
ANNUAL_TWH = {
    "gas_dhw":       64.7,   # U3 55.6 + U5 hot water 9.1
    "elec_space":    21.2,   # U3 13.7 + U5 heating 7.5
    "elec_dhw":       5.4,   # U3 3.8  + U5 1.6
    "oil_space":     45.7,   # U3 21.9 + U5 23.8
    "oil_dhw":        5.3,
    "bio_space":     23.8,   # U3 bio 12.1 + U5 'other' 11.7
    "bio_dhw":        3.5,
    "heat_networks":  6.2,
    "solid":          2.0,
    "cooling_vent":  10.4,   # U5 cooling & ventilation electricity
}
```

Note that **gas space heating is absent from this dict**. That is deliberate
and is the subject of Section 4.

**For Ireland this is the first thing to resolve.** SEAI's National Energy
Balance and its residential end-use work are the equivalent, but the table
structure is not the same and the domestic/services boundary is drawn
differently. Do not port these numbers; port the *method* of taking space and
water as separate published line items and refusing to derive one from the
other.

---

## 3. Shaping: the same annual, two different curves

Each weekly figure is an annual total multiplied by a shape factor. There are
three, computed once per week:

```python
f_flat = 7.0 / 365.0                    # constant: 1/52 of the year
f_h    = hdd_week / hdd_trailing_12m    # heating degree day share
f_c    = cdd_week / cdd_trailing_12m    # cooling degree day share
```

And they are applied by end use, not by fuel:

```python
"gas_dhw":   A["gas_dhw"]   * f_flat,
"oil":       A["oil_space"] * f_h  + A["oil_dhw"]  * f_flat,
"elec_heat": A["elec_space"]* f_h  + A["elec_dhw"] * f_flat,
"bio_other": A["bio_space"] * f_h  + A["bio_dhw"]  * f_flat,
```

**A fuel that does both jobs appears twice, on two different curves.** Oil is
not "oil" — it is oil-for-space on the degree-day curve plus oil-for-water on
the flat curve, summed for display. That matters more in Ireland than in
Britain, because oil is a much larger share of the ROI heat mix.

**One trap.** `f_h` and `f_c` must be computed on the **trailing 365 days**, not
on whatever regression window you happen to be using. The UK site raised its
regression window from 365 to 730 days on 11 August 2026 and every quantity
that treated the window as a year silently doubled — the calibrated annual gas
space heat went from 261.8 to 531.5 TWh and the electrification limit halved
from 98% to 49%. All four test suites passed throughout, because they check
structure and consistency, not whether an annual quantity spans a year.

The fix was to separate the constants (`WINDOW_DAYS = 730`, `ANNUAL_DAYS =
365`) and add a plausibility gate: trailing-12-month HDD must fall in
1,200–3,200 or the build fails. **Build that gate in from the start.** It costs
four lines and it is the only thing that catches this class of error.

---

## 4. Why the split makes the live estimate possible

This is the part worth understanding properly.

Gas space heating is **not** taken from the annual table. It is estimated live
from daily gas offtake to the distribution zones, via a segmented regression on
temperature:

    G_i = α + β · HDD_i + ε_i

where G is daily gas delivered to buildings. The two fitted parameters are not
of equal interest:

- **β · HDD** is the temperature-responsive component. This *is* space heating,
  measured every day from a metered national quantity.
- **α** is everything that does not respond to temperature: hot water, cooking,
  and non-heating industrial gas on the distribution network.

Current UK values: α ≈ 476 GWh/day, β ≈ 130 GWh per degree day, base 16.5 °C,
R² ≈ 0.92 on a 730-day window.

**So the DHW/space separation is not applied to gas — it emerges from the
regression.** The intercept *is* the non-heating gas. That is why the site can
say "gas space heating is estimated live from grid data; other sources are
official annual figures shaped by the weather" and mean it literally.

**Two consequences.**

The intercept is not pure hot water. It contains cooking and distribution-
connected industry, and the UK site says so in the legend rather than claiming
otherwise. Do not label α as "hot water".

And the regression must be run on **distribution-zone** gas, not transmission
totals. GB LDZ demand is of order 480 GWh/day at summer minimum against an NTS
total of 1,000–1,400 GWh/day; the difference is power stations and large
industry, which does not track degree days. Using the transmission total
introduces a large weather-insensitive term and depresses the apparent
temperature sensitivity.

**For Ireland:** Gas Networks Ireland is the equivalent operator and the same
distinction applies. The bigger question is coverage — the Irish gas network
reaches a much smaller share of dwellings than the GB one, so a gas-based live
estimator measures a smaller slice of total heat than it does in Britain. That
does not invalidate the method; it changes what fraction of the picture is live
versus anchored, and the site should say which.

---

## 5. Calibration and the reconciliation ratio

The regression yields a self-consistent daily signal but not an absolute
national total. The trailing-twelve-month integral of the fitted space-heating
component is therefore reconciled against the published annual:

    r = (modelled 12-month space heat) / (published anchor, weather-normalised)

The UK publication gate is |r − 1| ≤ 0.10. The current value is r ≈ 1.10 — at
the edge of tolerance, and **reported openly on the site rather than silently
rescaled**. That is a deliberate choice: a visible 10% residual is more useful
to a reader than a hidden correction factor.

Candidate contributions to the residual are named rather than resolved: the
treatment of non-domestic gas, the fixed base temperature, and
weather-normalisation differences between the trailing window and the anchor's
reference year.

**Recommend the same discipline for Ireland.** Publish the ratio, publish the
gate, and do not tune the model until it agrees.

---

## 5a. Separate efficiencies, added 13 August 2026

**The UK site now converts the two end uses at different boiler efficiencies.**
This was the Irish site's finding and the UK has adopted it; both dashboards
are now consistent.

A boiler serving only a cylinder fires in short bursts, cools between them, and
returns water above the condensing dewpoint — so it loses its latent recovery
outright. SAP has carried separate winter and summer seasonal efficiencies for
this reason since SAP 2009.

```python
EFF     = {"gas": 0.835, "oil": 0.82, "bio": 0.70, "solid": 0.55}  # winter
EFF_DHW = {"gas": 0.73,  "oil": 0.71}                              # summer
```

Derived as the mean SAP 2012 Table 4b summer/winter ratio applied to the
existing winter anchor, so the sourced winter figures are untouched:

- gas: 64/74, 74/84, 65/74, 75/84 → mean 0.879 × 0.835 = 0.73
- oil: 68/80, 72/84, 73/82 → mean 0.866 × 0.820 = 0.71

Oil comes out identical on both sites, since both use 0.82 as the winter
anchor. Summer runs 82–89% of winter across the whole table.

**Four implementation points.**

1. **The summer figure applies year-round**, not seasonally. SAP is explicit
   that η_water = η_summer for all twelve months. The distinction is between
   end uses, not between seasons.
2. **Gas and oil only.** Table 4b does not cover bioenergy or solid fuel;
   those keep a single figure and the code says so rather than inventing a
   ratio.
3. **Keep the components separate through the efficiency step.** The UK site
   summed `oil_space` and `oil_dhw` into one `mix["oil"]` before applying an
   efficiency, so the split had to be recovered. Do not sum first.
4. **Check every downstream use.** Applying it in one place is not enough. The
   UK change touched four: the useful conversion, the waste band, the national
   annual heat total, and the retro store's `dhw_annual_TWh` — which is part of
   the denominator of the electrification limit.

**Effect.** Useful annual hot water fell 66.2 → 58.8 TWh; the space-plus-water
total fell 1.8%, so the electrification limit rose by about the same. More
visibly, the gas line acquired a seasonal efficiency shape it did not have
before: about 0.81 in January against **0.74 in July**, when the load is almost
entirely hot water. It was a flat 0.835 in every month.

**Two caveats carried in the code comment.** Table 4b is SAP's fallback for
boilers absent from the Product Characteristics Database, so it is conservative
and skewed to older plant; a PCDB-weighted fleet figure would beat it. And
Table 4c deducts five points from both figures where a regular boiler has no
interlock, which neither site applies.

---

## 6. Downstream: where the split shows up again

Once separated, the two stay separated through the rest of the model.

**Pricing.** The domestic/non-domestic blend differs by end use — UK gas space
heat is 73.5% domestic, gas DHW 85.9% — so they are priced at different
weighted tariffs. A single "gas price" would be wrong for both.

**The hourly engine.** The retrospective store carries them as separate
annuals, `space_annual_TWh` 304.8 and `dhw_annual_TWh` 66.2. Their sum, 371
TWh, is the denominator of the electrification limit. When the annual-days bug
doubled both, the limit halved — which is how the bug was noticed.

**Hourly shaping.** Space heat is shaped across the day by degree-hours with a
thermal-mass damping term; hot water takes a fixed diurnal profile. Same
principle, finer resolution.

**Heat pump flow temperature.** DHW needs a higher flow temperature than space
heating — 60 °C-plus for legionella control against 30–50 °C weather-compensated
for space. On a shared header the hot water requirement pins the flow for the
whole building. This is not modelled on the UK site yet and is flagged as an
opportunity; if the Irish site models it, that would be new ground.

---

## 7. Checklist for the Irish build

1. Take space and water as **separate published line items** from SEAI. Do not
   derive one from the other or apply a domestic ratio to a national total.
2. Shape space on degree days, water flat. Apply **by end use, not by fuel** —
   oil appears on both curves.
3. Compute shape factors on the **trailing 365 days**, whatever the regression
   window is. Add the plausibility gate before you need it.
4. Convert the two end uses at **different boiler efficiencies** (Section 5a),
   and check every downstream use — useful, waste, national total, and any
   hourly store.
5. Run the live regression on **distribution-network** gas only, and state what
   share of national heat that network actually reaches.
6. Label the intercept honestly — it is non-heating gas, not hot water.
7. Publish the calibration ratio and its gate. Do not tune to close it.
8. Carry the two annuals separately into any hourly engine; their sum is the
   denominator of every share you will later quote.

---

## 8. What is different about Ireland, and needs deciding rather than porting

- **Oil is the main series**, not a secondary one. The space/water split on oil
  therefore carries far more weight than it does in Britain, and the DHW share
  of oil is worth sourcing properly rather than inheriting a UK ratio.
- **Gas network coverage** is much lower, so the live fraction of the estimate
  is smaller. Say so on the page.
- **Two jurisdictions.** NI and ROI have different statistical sources,
  different network operators and different degree-day conventions. The UK site
  treats NI as a separate estimated block precisely because the GB gas feeds do
  not cover it.
- **Degree-day base.** The UK site scans 14.5, 15.5 and 16.5 °C and selects on
  goodness of fit, currently landing on 16.5. Do not assume the Irish stock
  gives the same answer; scan it.

---

*Questions to spt@causewaygt.com. The UK implementation is in
`scripts/build.py` — the relevant parts are the `ANNUAL_TWH` dict, the shape
factors around line 316, and the segmented regression in the calibration
block.*
