# UK Heat Split — a weekly estimate of how Britain heats and cools itself

Most of Britain's heat still comes from burning gas, and a material share of
every unit burned never becomes useful heat. This site turns live grid data
into a weekly estimate of the GB heating and cooling energy split — the
energy volume, what it costs, what it emits, how much of it is UK-indigenous,
who is left sweltering without cooling at all, what geothermal (deep, mine
water, and ground source) supplies today and could supply next — and, at the
foot of the page, why heat is the energy transition's biggest prize.

**Live site:** https://causewaygt.github.io/uk-heatsplit/
**Full methodology:** [UK Heat Split: data sources and estimation methodology, v8 (PDF)](docs/uk-heatsplit-methodology.pdf)

The page is organised as six numbered sections: **1** the starting point,
**2** what heat costs and emits, **3** electricity grid impacts, **4** the
rising demand for cooling, **5** UK geothermal now and next, and **6** value
for money. Footnotes follow.

Every live figure is a model estimate, not a measurement. Every figure resting
on Causeway judgement is marked † and is open to challenge at
contact@causewaygt.com.

## What the dashboard shows

- **Headlines** — four stats: energy purchased this week; the UK-indigenous
  share of heat and cooling delivered (services basis); the national bill
  split between heating and cooling; and heating & cooling emissions —
  alongside a what-if strip answering all four with 20% of heat and cooling
  moved to geothermal.
- **Trends** — a sparkline under each headline showing its weekly history,
  with a 1-week to 12-month window selector that also re-totals the
  headline figures themselves — actuals and the 20% what-if strip alike —
  so the 12-month view states the year's purchased energy, bill, emissions
  and the cumulative what-if saving (sums over complete live weeks;
  the indigenous share as an energy-weighted average). The what-if is
  overlaid in green on each sparkline; the shaded band is the forgone
  saving, and every splittable headline — purchased energy, bill and
  emissions, actuals and what-if alike — carries its heat/cool split
  in each window's caption. Live weeks only: each point is computed with
  the same estimators as the headline, priced at the Ofgem cap in force
  that week and carboned at that week's mean grid intensity — no modelled
  back-cast. The window is bounded by the ~13 months the National Gas feed
  serves; a longer modelled series, visually distinguished, is planned.
- **Dual bars, same scale** — energy in (fuel and electricity purchased) vs
  useful heat and cooling delivered: combustion derated by in-situ boiler
  efficiencies, heat pumps credited with their harvested ambient heat,
  cooling with its delivered multiple.
- **Daily spark gap** — wholesale cost of useful heat via a gas boiler vs a
  ground-source heat pump, updated daily from the National Gas SAP and the
  Elexon market index.
- **What heat costs** — pence per useful kWh by route at current Ofgem cap
  rates, and the national weekly bill — priced by sector: each fuel's
  domestic share (ECUK) at the Ofgem cap, its services share at
  DESNZ QEP-anchored non-domestic rates†, standing charges excluded.
- **What heat emits** — weekly emissions with a heat/cool split, and gCO2e
  per useful kWh by route: combustion at fixed DESNZ factors (natural gas
  0.18296 kgCO2e/kWh, gross CV basis), electric routes at the live GB grid
  intensity (NESO Carbon Intensity API, 7-day mean) — so the heat-pump rows
  fall as the grid decarbonises while combustion never does.
- **The empty bar** — the 20% what-if priced in hardware: installed
  thermal megawatts of UK ground-source capacity today (861 MWth,
  EGC 2025 country update) against the ~30–40 GWth the what-if requires at 2,000
  equivalent full-load hours†, beside installed reality in France, the
  Netherlands and Sweden† (comparator constants shared with the Irish
  sibling). Sweden's whole fleet — Europe's largest — covers barely a
  quarter of the UK requirement; per person the what-if is unremarkable
  (Sweden 778 W vs UK 13 W†), enormous only because Britain has never
  started. A calibration chart restates the same fleets as a share of
  each country's own buildings heat†: UK 0.5%, France ~2%, Netherlands
  ~5%, Sweden ~20% — Sweden's existing fleet already serves about the
  share the UK what-if proposes.
- **Geothermal — now and next** — heat and cooling from geothermal this week,
  plus annual bars for today, 2027, 2031 and 2050, each tagged to its source.
  Today's heat is anchored on the EGEC 2025 UK Country Update (~1.43 TWh/yr
  from ~55,210 GSHP units, 2023 base) plus mine-water, deep and open-loop
  district schemes; the 2027 and 2031 bars carry the EGEC Geothermal Market
  Report 2025 evidence (4,070 UK unit sales, 4 new large closed-loop systems,
  ~11 UK district systems in development). A benchmark line sets the UK
  against Europe: 2.55 million geothermal heat pumps delivering 88 TWh in
  2025 — Sweden alone sold 26,785 units to the UK's 4,070.
- **The gas engine room** — daily gas offtake to the distribution zones
  (buildings) against the regression-estimated space-heating signal, with
  total NTS shown for context. The fit reports R², the residual standard
  error (the honest daily uncertainty), and the calibration ratio against the
  ECUK anchor with its ±10% publication gate.
- **Cooling: demand vs delivery, in three tiers** —
  *Tiers 1 & 2 (the equipped fleet):* an observed cooling response curve from
  half-hourly national electricity demand (NESO, embedded solar and wind
  reconstructed, centred within month and weekend class), binned by cooling
  degree days: what installed cooling delivers, and whether it saturates.
  A reconciliation diagnostic — a year-round regression of demand on
  heating and cooling degree days jointly, with the cooling base swept so
  the data chooses where conditioning starts — decomposes the gap between
  the observed response and the ECUK cooling & ventilation anchor into
  weather-driven and weather-flat parts (published, not yet in the bill).
  *Tier 3 (the comfort deficit):* the buildings with no cooling at all —
  overheating-degree-hours above the CIBSE 26°C threshold (population-
  weighted, live from hourly ERA5) × the unequipped stock at risk (bounded
  low/central/high from EHS self-reports to the CCC's over-half-at-risk),
  with the health and productivity context (ONS/UKHSA excess deaths, ONS
  hot-day output losses). A tier bar graphic sets the unserviced deficit
  against what the fleet delivered, and a seasonal-mirror graphic shows the
  geocooling dividend: rejected summer heat banked underground and ~70%
  recovered for winter heating (UTES round-trip, literature range 50–80%).
- **Northern Ireland** — a separate estimate: NI runs on its own gas and
  electricity systems, so the live GB feeds do not cover it. If the NI DfE
  geothermal licensing proposals proceed (consultation 2026; heat below
  100 m depth), the resulting register would be the UK's first mandatory
  geothermal data source, and this dashboard is designed to ingest it.
- **WHY HEAT?** — the zoom-out. Four annual pies place heat beside transport
  and power across energy services delivered (TWh), national spend (£bn),
  imported energy (TWh) and emissions (Mt CO2e). The pattern they show: heat
  is the largest energy service in Britain, the cheapest per unit (untaxed
  fossil fuel), a major import driver, and a top-tier emitter — which is why
  it is the transition's biggest prize.

- **Electricity grid impacts (v7)** — the same 20% what-if, computed
  hour by hour across the trailing twelve months against the electricity
  system Britain actually ran. Three views (live week, last 90 days, the
  calendar-year falcon curve), two stress statistics, the tightest week
  under a capacity line, and the COP curves the whole thing rests on.
  See **The hourly engine** below.

## The hourly engine (v7, extended at v8)

The weekly panels describe the transition; this one tests it against the
grid. A second engine (`scripts/retro.py`) keeps a rolling hourly store —
`docs/retro.json`, append-only with a 2-day revision window — of
temperature, wholesale price, grid carbon intensity, observed underlying
demand, and observed wind and solar. Every annual figure is computed on the
trailing 8,760 hours.

**Two winters, from v8.** The store cap was raised from 400 to 800 days and
backfilled to June 2024, so the worst hour, the binding hour and the tightest
week are now *chosen across* two winters rather than describing the only one
available. The first run on the extended store left every figure unmoved:
2025/26 still holds the binding hour, and it holds it against 2024/25's own
lower capacity block. That is a stronger claim than before, not a new number.

**What is modelled and what is measured.** Heat demand is modelled: annual
useful heat is distributed by daily heating-degree-day share, then within
the day by degree-hours, with hot water flat. Everything else — price,
carbon, demand, wind, solar — is measured. Days below 1 heating degree day
carry no space heat (ramping to full at 3†), because raw degree-hour
allocation otherwise assigns winter-strength heat to cool summer nights,
which the weekly estimator itself contradicts; weights are renormalised so
the annual total is unchanged.

**The three routes.** Each hour's replacement electricity is a Carnot
fraction with weather-compensated flow (30–50 °C) for space heat and a
fixed 52 °C for hot water†, sources being outdoor air less 3 K, ground at
8 °C, and networks at 19.6 °C† — a *charged* ambient loop, warmer than virgin
ground because summer heat has been rejected into it. A single scale factor per route is solved so
the year integrates exactly to the published seasonal performance factors
(2.80 / 3.24 / 5.0†). The flow curve is the same for all three routes
deliberately: the what-if replaces heat in the existing building stock, so
weather compensation applies whatever the source. One consequence worth
knowing: above about 11 °C, air-source outperforms ground source, because
outdoor air less its approach is then warmer than the ground.

**Two different stress figures.** The *coldest hour* is the year's peak
modelled heat demand and reports each route's addition alone — gross, and
separated by the ratio of coefficients of performance. The *binding hour*
is the hour of greatest total call on dispatchable supply (observed demand
plus the net addition, less the wind and solar actually generated), and
reports a system total dominated by standing demand. Net subtracts the
fifth of existing resistive heating the what-if displaces; that credit is
space-shaped, so night-storage profiles would reduce it at peaks† and the
gross figures bound the zero-credit case. The two hours need not coincide.

**The capacity line.** A de-rated block of dispatchable and interconnector
capacity **per winter** — 60.6 GW for 2024/25 and 62.3 GW for 2025/26†,
derived identically from each year's NESO Winter Outlook as ACS peak plus
de-rated margin less de-rated wind — plus the wind and solar actually
generated. Wind is excluded from the block because outturn wind is added
hourly; leaving it in would count it twice. Each winter is tested against its
own fleet, and with a ceiling that varies the binding hour is the hour of
*least headroom* rather than of highest absolute requirement. Renewables enter as
observed rather than de-rated, which removes the most contested assumption
in margin arithmetic and lets the line sag when the wind genuinely
dropped. It is a **static overlay, not a dispatch model**: no redispatch,
imports response, demand-side response or price response, no distribution
constraints, and a GW figure cannot represent storage duration. Exceedance
is reported as hours above available headroom under today's fleet, never
as a claim about supply interruption.

**Scope of the cooling figures.** Cooling here is the ECUK *buildings*
anchor. From v8 that anchor is split **54% ventilation / 46% cooling** using
the BEES component lines it derives from, and **ventilation is reported as its
own service rather than as cooling** — fans move air, not heat, so there is no
coefficient of performance to apply. That correction reduced published cooling
delivered by about a third. Data centres, cold store and the wider cold chain
sit outside the anchor entirely†; they are named on the cooling panel as
Tier 0, of order 26 TWh/yr, and deliberately not plotted, being several times
the tiers above them.

**Gates.** Four hourly checks run before anything is published: the
performance anchors are reproduced; the hourly network route reconciles with
the weekly estimator (near-tautological by design — it proves wiring, not
physics); cooling is conserved against its annual; and any feed missing more
than 5% of its hours is refused. A failed gate leaves the previous hourly
output in place and the weekly panels untouched.

**Three whole-build gates were added at v8**, each after a defect reached
publication. *Annual plausibility*: trailing-12-month HDD must fall in
1,200–3,200, because raising the regression window from 365 to 730 days
doubled every quantity that treated the window as a year — the electrification
limit read 49% instead of 98% for a day — while all four test suites passed.
*Document structure*: nothing before the doctype, balanced script tags, no
JavaScript in markup; `node --check` proves a script parses, not that the
document is well formed. *Scope*: `main()` is walked for names read but never
assigned there, which a syntax check cannot see.

## Autumn maintenance — things that expire

These are constants and anchors that go stale on a known schedule:

| When | What | Why |
|---|---|---|
| October | **NESO Winter Outlook 2026/27** → add a 2026 row to `DISPATCH_BY_WINTER` in `scripts/retro.py` | Each winter is tested against its own fleet; an unmapped year falls back to the latest and should not |
| Autumn | **ECUK 2026** re-anchor (heat-pump electricity, domestic shares, sectoral revisions) | Annual anchors lag 9–15 months |
| September | **Cooling reconciliation gate** — decide whether the undemeaned baseline model replaces the ECUK-anchored cooling estimate | Recovers 74% of the anchor against the centred curve's 15%; switchover would restate summer figures |
| Spring | **`RETRO_BACKFILL`** must remain `False` between deliberate backfills | `True` refetches ~800 days of four feeds every night instead of two days |
| Quarterly | **Ofgem cap history** | Bill layer |

**Two version counters, not one.** `history_schema` tracks new *fields* on
the weekly history — a bump adds them and restates stored weeks so the new
fields appear. `anchor_epoch` tracks a changed *basis* — bump it whenever a
constant changes what a past week would have computed, and every
recomputable stored week is rewritten. A schema bump alone would leave the
series half on one basis and half on another.

## Method in one paragraph

Daily gas offtake to Britain's local distribution zones (LDZ — the network
serving homes and most businesses, excluding directly connected power
stations) is regressed against population-weighted GB heating degree days
(ERA5 reanalysis via Open-Meteo). The temperature-sensitive component is
attributed to space heating, following the published Watson et al. / Sansom
method; the trailing 12-month total is calibrated against the DESNZ ECUK
end-use tables, GB-adjusted and weather-normalised (current ratio ~1.10,
within the ±10% publication threshold). Other fuels and cooling take their
annual levels from ECUK 2025 (calendar 2024) shaped by heating/cooling degree
days. **Space heating and hot water are converted at different boiler
efficiencies** — gas 0.835 and 0.73, oil 0.82 and 0.71 — because a boiler
serving only a cylinder loses its latent recovery; the hot-water figures come
from SAP 2012 Table 4b and apply year-round, not seasonally — cooling on a 15.5 °C base, the balance point at which comfort cooling
plant begins to run, not the Met Office's 22 °C climate convention. The indigenous share is measured on a services basis — each unit of
delivered heat or cooling inherits the UK-origin share of its energy input,
with harvested ambient/ground heat counting as 100% indigenous, consistent
with Eurostat/DUKES renewable-supply accounting. **The full technical
methodology — with equations, figures and a complete bibliography — is in
[docs/uk-heatsplit-methodology.pdf](docs/uk-heatsplit-methodology.pdf).**

## Estimates open to challenge

Sourced figures (Ofgem cap, ECUK anchors, DESNZ GHG factors, Energy Systems
Catapult SPFs, EGEC capacity and market data, NISRA heating shares, MCS
installations, EHS/CCC overheating prevalence, ONS/UKHSA health and
productivity data) are cited as such. Figures resting on Causeway judgement
are marked † on the site — the geothermal forecast scenario, several unit
prices, the cooling EER (3.0, range 2.5–4.0 — the largest single unverified
assumption in the cooling chain), the comfort-deficit stock fractions and
thermal-response coefficients, the network source temperature and SCOP, the
UTES round-trip, the chiller and cooling-increment capex in the value panel,
indigenous input-origin shares, the NI heat total, and the WHY HEAT?
service-level allocations. Challenge and input welcome:
**contact@causewaygt.com**.

- **The hourly heat shape** is modelled from weather with no occupancy
  model; the summer damping threshold and the flow temperatures are
  conventions†, chosen to reconcile with the weekly estimator.
- **The capacity line** is now two winters of observed weather, each under
  its own de-rated block†, and still says nothing about storage duration or
  network constraints. Two is not a distribution.
- **The observed cooling response is a lower bound**, not an independent
  estimate: within-class centring removes the level along with the seasonal
  confound, so it recovers about a seventh of the anchor. The undemeaned
  reconciliation recovers 74% and is not yet used in published figures.
- **Humidity was tested and rejected.** Dewpoint adds 0.0014 to R² and
  moist-air enthalpy 0.0001; the quadratic term rose 7% where latent load
  would have made it fall. The convexity is the extensive margin — buildings
  switching on — not latent load.
- **The displaced resistive credit** assumes the what-if samples the
  existing stock proportionally and that displaced load is space-shaped†;
  gross and net figures bound the truth between them.

## Data sources & licences

National Gas Transmission open data (demand and SAP publications, REST API;
derived public use and attribution confirmed with the operator, July 2026) ·
Contains BMRS data © Elexon Limited copyright and database right 2026 · NESO
Data Portal (demand) and NESO Carbon Intensity API, NESO Open Licence ·
Open-Meteo.com (CC BY 4.0) / Copernicus ERA5 (daily and hourly) · DESNZ ECUK,
DUKES and GHG conversion factors, MHCLG dwelling statistics, and NISRA
statistics, under the Open Government Licence v3.0 · EGEC 2025 UK Country
Update and EGEC Geothermal Market Report 2025, English Housing Survey, CCC
adaptation reporting, ONS/UKHSA heat mortality and productivity statistics
(cited). Full bibliography with DOIs in the methodology PDF.

Hourly layer (v7): ERA5 reanalysis via Open-Meteo (temperature, twelve
population-weighted points); Elexon BMRS market index (wholesale price) and
FUELINST (transmission wind); NESO historic demand and daily demand update
(underlying demand, embedded wind and solar) and Carbon Intensity API;
NESO Winter Outlook 2025/26 (the de-rated capacity block†). All open data,
no authentication.

## How it runs

A GitHub Actions cron (`.github/workflows/update.yml`) runs daily at 03:43
UTC: `scripts/build.py` pulls the feeds, fits the regressions, computes the
mix, costs, emissions, cooling tiers and headlines, and commits
`docs/data.json`; the static page (`docs/index.html`, Plotly) renders it.
Feed failures fall back to the last good values and are flagged on the page
(sources that publish on a lag show amber); build failures push a
notification. Site traffic is measured with GoatCounter (cookieless, no
personal data). No API keys required; fork-friendly.

The daily commit of `docs/data.json` also carries the weekly trend history
(capped at 60 weeks), so the git log doubles as its backup: each run appends
the newest complete week, recomputes the two most recent against feed
revisions, and leaves older weeks frozen as first published.

Anchor constants are refreshed on a maintenance calendar: Ofgem cap
quarterly (one row added to the cap-history table in `build.py`, so past
weeks keep the cap that was in force); ECUK/DUKES and the WHY HEAT? panel
annually; the geothermal panel annually on MCS/EGEC release.

## Repository layout

```
docs/
  index.html                      the dashboard (static; Plotly)
  data.json                       written daily by the workflow
  uk-heatsplit-methodology.pdf    full technical methodology statement
scripts/
  build.py                        pulls feeds, fits, computes, writes data.json
  retro.py                        hourly engine: store, calibration, gates,
                                  slices, system view
  fetch_*.py                      per-source fetchers
analysis/                         offline, hand-run — not on the daily path
  fetch_humidity.py               dewpoint and moist-air enthalpy (a null result)
  fit_cooling.py                  undemeaned baseline model, balance-point scan
docs/retro.json                   ~800-day hourly store, append-only
.github/workflows/update.yml      daily cron
.github/workflows/cooling-fit.yml manual: runs the offline cooling fit
```

## Versioning

The site carries a version (footer, `SITE_VERSION` in `docs/index.html`):
x.y.z where **x** = new data source or panel, **y** = update to an existing
source or anchor, **z** = wording or formatting. Current: **8.31.1**.

History: v1 launch (gas split, costs, spark gap, geothermal, NI) → v2 carbon
layer → v3.0–3.2 observed cooling analysis → v3.3–3.4 comfort deficit, tier
graphic and UTES dividend → v3.5 services-basis indigenous share → v3.6 EGEC
refinements → v4.0 WHY HEAT? whole-economy panel → v4.0.1–4.0.3 methodology
statement and calibration ratio surfaced → v5.0–5.2 live trend layer, window
selector, cooling reconciliation diagnostic → v6.0–6.7 the empty bar,
share-of-national-heat calibration, DUKES 2026 re-anchor, heat/cool splits at
every window → **v7.0 the hourly engine and the grid impacts panel** →
**v8 the cooling rebuild, the value case and the price series**.

### What v8 changed

**The site was restructured** into six numbered sections with a value-for-money
panel at the end, published after being held while its cooling component was
costed.

**Cooling was rebuilt from the anchor outward.** The ECUK cooling-and-
ventilation line is now split 54:46 from BEES rather than 50/50 by assumption;
ventilation is reported as its own service because fans have no coefficient of
performance, which cut published cooling delivered by about a third; the shape
base moved from 18 °C to 15.5 °C; the binned response curve became a continuous
quadratic, removing a ceiling that held fifteen days a year at one value; and
the saturation tier was withdrawn, because the measured response steepens where
that tier assumed it flattens.

**Boilers were given a separate hot-water efficiency**, from SAP Table 4b, so
gas now carries a seasonal efficiency shape — about 0.81 in January against
0.74 in July, when the load is almost all hot water. It was a flat 0.835 in
every month before.

**Three things were tested and reported as negative results** rather than
quietly dropped: humidity, the slope-to-level inversion, and the saturation
tier. All three are in the methodology.

**The hourly store holds two winters**, each tested against its own NESO
capacity block. **COP is now evaluated at each day's temperature** rather than
as a fixed seasonal factor, which is what makes the heat-price series honest —
air-source and ground source cross, and the crossing is the point.

**A heat-price series was added** for four routes on two bases, wholesale and
at the meter, with the difference between them showing what policy costs do to
the comparison. It also carries **three services** — space heating, hot water,
and both blended as delivered. Hot water runs at a 52 °C cylinder flow all
year, so none of the flow-temperature benefit is available to it and the
geothermal advantage is about 1.7× in every month, against 1.9–4.4× on space
heating. The blended view is a harmonic mean weighted by the service mix
actually served that day, and is the only one that moves with the load. **The spark-gap ticker carries both bases** for the same
reason.

**Twelve defects were found and declared**, and are tabulated in §13 of the
methodology. Four came from an external peer review on 17 August 2026 and are
the most serious: embedded solar was added twice in three cooling diagnostics,
which made the diurnal comfort/process separation an artefact and invalidated
the 74% cooling reconciliation — both now withdrawn; the network source
temperature was presented as an independent check on the Carnot fraction when
it is derived to produce that agreement; the front end added the air-source
approach temperature instead of subtracting it; and "electrification limit"
implied capacity-adequacy modelling the calculation does not do. Three new
build gates were added earlier in v8, and none of them would have caught these:
a slope diagnostic has no plausible range to gate against.

*A Causeway Energies public-interest tool — https://causewaygt.com*
