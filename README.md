# UK Heat *Shift* — the strategic case for GW-scale geothermal heating and cooling in the UK

Most of Britain's heat still comes from burning gas, and a material share of
every unit burned never becomes useful heat. This site sets out the strategic
case for geothermal heating and cooling at gigawatt scale — and puts a live,
public estimator underneath it, so the case can be checked rather than taken
on trust.

It is an advocacy tool, and says so. What distinguishes it from resource maps
and power-generation prospectuses is that it argues from **heat and cooling
delivered**, estimated weekly from live grid data, and publishes the working.

**Live site:** https://causewaygt.github.io/uk-heatsplit/
**Full methodology:** [UK Heat Shift: data sources and estimation methodology (PDF)](docs/uk-heatsplit-methodology.pdf)

Every live figure is a model estimate, not a measurement. Every figure resting
on Causeway judgement is marked † and is open to challenge at
contact@causewaygt.com.

## The case in six figures (v8)

The frontispiece is the first thing on the page: six figures in order — big,
cheap, feasible, growing, financeable, overdue. Each is computed from the
panels below and restates with them; none is typed.

| | The claim | Where it comes from |
|---|---|---|
| **01** | Heat is the largest thing Britain buys, and it is still fire | 12-month purchased energy, bill, emissions, combustion share |
| **02** | Geothermal heat is already the cheaper commodity | the daily spark gap |
| **03** | Only geothermal networks fit inside the grid Britain already has | the electrification limit (hourly engine) |
| **04** | The same asset does cooling — and cooling is the part that grows | the comfort deficit and the UTES dividend |
| **05** | Two fifths of the build pays for itself out of the energy it saves | the capital panel |
| **06** | Britain is the outlier, not the pioneer — all the technology is proven | installed hardware, four countries |

Two properties are deliberate and pinned by tests.

The frontispiece is **fixed to the trailing twelve months** whatever window
the reader selects below. In an August week Britain draws almost no heat, and
a strategic case cannot be read off one of them.

Point 05 **reads the capital panel** rather than carrying its own copy of the
arithmetic, so the summary and the panel it summarises cannot disagree. It is
also the one figure printed in ember rather than green: it argues against the
case, and colouring it like the rest would look like hiding it.

A floating control offers a way back to either the case or the starting point
once the reader has scrolled past them.

## What the dashboard shows

- **The starting point** — four headline stats: energy purchased; the
  UK-indigenous share of heat and cooling delivered (services basis); the
  national bill split between heating and cooling; and heating & cooling
  emissions — alongside a what-if strip answering all four with 20% of heat
  and cooling moved to geothermal. The heading names the window the reader
  has selected, and the window selector sits directly beneath it.
- **Trends** — a sparkline under each headline showing its weekly history,
  with a 1-week to 12-month window selector that also re-totals the
  headline figures themselves — actuals and the 20% what-if strip alike —
  so the 12-month view states the year's purchased energy, bill, emissions
  and the cumulative what-if saving (sums over complete live weeks; the
  indigenous share as an energy-weighted average). The what-if is overlaid
  in green on each sparkline; the shaded band is the forgone saving, and
  every splittable headline carries its heat/cool split in each window's
  caption. Live weeks only: each point is computed with the same estimators
  as the headline, priced at the Ofgem cap in force that week and carboned
  at that week's mean grid intensity — no modelled back-cast. The window is
  bounded by the ~13 months the National Gas feed serves; a longer modelled
  series, visually distinguished, is planned.
- **Dual bars, same scale** — energy in (fuel and electricity purchased) vs
  useful heat and cooling delivered: combustion derated by in-situ boiler
  efficiencies, heat pumps credited with their harvested ambient heat,
  cooling with its delivered multiple. The bars re-total with the window
  selector, from a per-fuel block carried on every stored week.
- **Daily spark gap** — wholesale cost of useful heat via a gas boiler vs a
  ground-source heat pump, updated daily from the National Gas SAP and the
  Elexon market index. It moves sharply day to day: quote it as live.
- **What heat costs** — pence per useful kWh by route at current Ofgem cap
  rates, and the national weekly bill — priced by sector: each fuel's
  domestic share (ECUK) at the Ofgem cap, its services share at
  DESNZ QEP-anchored non-domestic rates†, standing charges excluded.
- **What heat emits** — weekly emissions with a heat/cool split, and gCO2e
  per useful kWh by route: combustion at fixed DESNZ factors (natural gas
  0.18296 kgCO2e/kWh, gross CV basis), electric routes at the live GB grid
  intensity (NESO Carbon Intensity API, 7-day mean) — so the heat-pump rows
  fall as the grid decarbonises while combustion never does.
- **The empty bar** — the 20% what-if priced in hardware: installed thermal
  megawatts of UK ground-source capacity today (861 MWth, EGC 2025 country
  update) against the ~30–40 GWth the what-if requires at 2,000 equivalent
  full-load hours†, beside installed reality in France, the Netherlands and
  Sweden† (comparator constants shared with the Irish sibling). Sweden's
  whole fleet — Europe's largest — covers barely a quarter of the UK
  requirement; per person the what-if is unremarkable (Sweden 778 W vs UK
  13 W†), enormous only because Britain has never started. A calibration
  chart restates the same fleets as a share of each country's own buildings
  heat†: UK 0.5%, France ~2%, Netherlands ~5%, Sweden ~20% — Sweden's
  existing fleet already serves about the share the UK what-if proposes.
- **Does the saving pay for the build? (v8)** — the capital panel. See below.
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
  total NTS for context. The fit reports R², the residual standard error
  (the honest daily uncertainty), and the calibration ratio against the ECUK
  anchor with its ±10% publication gate — **currently outside that gate and
  reported as such on the page**. See below.
- **Cooling: demand vs delivery, in three tiers** —
  *Tiers 1 & 2 (the equipped fleet):* an observed cooling response curve from
  half-hourly national electricity demand (NESO, embedded solar and wind
  reconstructed, centred within month and weekend class), binned by cooling
  degree days: what installed cooling delivers, and whether it saturates.
  A reconciliation diagnostic — a year-round regression of demand on heating
  and cooling degree days jointly, with the cooling base swept so the data
  chooses where conditioning starts — decomposes the gap between the observed
  response and the ECUK cooling & ventilation anchor into weather-driven and
  weather-flat parts (published, not yet in the bill).
  *Tier 3 (the comfort deficit):* the buildings with no cooling at all —
  overheating-degree-hours above the CIBSE 26°C threshold (population-
  weighted, live from hourly ERA5) × the unequipped stock at risk (bounded
  low/central/high from EHS self-reports to the CCC's over-half-at-risk),
  with the health and productivity context (ONS/UKHSA excess deaths, ONS
  hot-day output losses). A tier bar sets the unserviced deficit against what
  the fleet delivered, and a seasonal-mirror graphic shows the geocooling
  dividend: rejected summer heat banked underground and ~70% recovered for
  winter heating (UTES round-trip, literature range 50–80%).
- **Northern Ireland** — a separate estimate: NI runs on its own gas and
  electricity systems, so the live GB feeds do not cover it. If the NI DfE
  geothermal licensing proposals proceed (consultation 2026; heat below
  100 m depth), the resulting register would be the UK's first mandatory
  geothermal data source, and this dashboard is designed to ingest it.
- **WHY HEAT?** — the zoom-out. Four annual pies place heat beside transport
  and power across energy services delivered (TWh), national spend (£bn),
  imported energy (TWh) and emissions (Mt CO2e). Heat is the largest energy
  service in Britain, the cheapest per unit (untaxed fossil fuel), a major
  import driver, and a top-tier emitter — which is why it is the transition's
  biggest prize.
- **Electricity grid impacts (v7)** — the same 20% what-if, computed hour by
  hour across the trailing twelve months against the electricity system
  Britain actually ran. Three views (live week, last 90 days, the
  calendar-year falcon curve), two stress statistics, the tightest week under
  a capacity line, the electrification limit, and the COP curves the whole
  thing rests on. See **The hourly engine** below.

## The capital panel (v8)

The only panel on the site driven by user inputs, and labelled as such:
**Calculator — not a live estimate.** It answers one question — does the
energy-bill saving pay for the build? — and is designed so a sceptic can move
every contested assumption and read the answer themselves.

**The live input** is the trailing-twelve-month saving: the difference between
what Britain paid for heat and cooling and what the 20% what-if would have
paid, summed over complete live weeks, and pinned to twelve months whatever
the trend selector shows. It is already net of the replacement electricity —
but it is a price differential, not a physical quantity, and it narrows if the
gas–electricity spread narrows.

**Unit capital is held per class in £ per annual MWh delivered, not per kW.**
Equivalent full-load hours run about 1,800 h for a domestic ground-source
system against 5,400–6,000 h for a baseload doublet, so a capacity-weighted
per-kW blend over-weights individual GSHP by three to four times. The classes
do not converge, and the separation is the substantive finding:

| Class | £ per annual MWh† | Full-load hours |
|---|---|---|
| ATES, above 2 MW | 100–260 | 2,000–2,500 h |
| Intermediate doublet | 290–320 | 5,400–6,000 h |
| Individual GSHP, vertical | 700–1,670 | 1,800 h |

**Eight levers, in two groups.** *Unit cost and finance*: network share
(weighted on heat delivered), the share of the saving captured by the investor,
cost of capital, tenor, distribution network, building connections.
*Over the build*: the deployment period, and the cost improvement reached by
its last year. Development and permits are added at 20%† as a constant rather
than a lever, because every published class anchor excludes them.

**The deployment period** spreads the capital across the build and ramps the
savings as the fleet completes. It moves coverage by about a point across its
whole 5–20 year range, because the two effects nearly cancel — which is the
reason to model it. It closes the objection that the panel assumes a fleet
that does not exist, rather than changing the answer.

**Cost improvement is a first-of-a-kind to mature-supply-chain level shift
reached by the last year of the build, not a compounding annual rate.** A rate
compounded across a decade implies a fall no heat infrastructure has delivered:
15% a year would leave unit capex at 23% of today's. The lever is capped at 40%
total and applies to plant and subsurface only. Trenching and building
connections are excavation and labour and do not learn — which is why, at the
default mix, 36% of the build is untouched by it, and why the gap does not
close by waiting.

Distribution and connections are **two levers, not one**: they scale on
different drivers, and the variance lives in connections — about £40 per
annual MWh for bulk supply to an anchor load against £900+ where every
dwelling is connected individually. Both fall on the **network share only**;
an individual ground-source install has no distribution main and no network
connection, and its pipework already sits inside its own class anchor.

The denominator is **heat and cooling delivered**: one bidirectional network
carries both, so its pipe is not charged to heat alone.

**The published default** — 70% network share, 70% capture, 6% over 30 years,
£250 distribution, £200 connections, a 10-year build and a 20% cost
improvement — returns **40% coverage**: £22bn of finance serviced against a
£56bn build, leaving a £34bn gap.

That the figure sits below 100% is deliberate. A stated gap is a policy
question honestly put; a self-financing claim would not survive contact with
anyone who builds these things. For scale, £34bn across a ten-year build is
£3.4bn a year — the same order as the Warm Homes Plan's £15bn over five years,
and roughly seventeen times the current heat-network commitment of £195m a
year. That comparison belongs in any policy conversation the number enters,
because the reader will make it within seconds.

## The hourly engine (v7)

The weekly panels describe the transition; this one tests it against the
grid. A second engine (`scripts/retro.py`) keeps a rolling 13-month hourly
store — `docs/retro.json`, append-only with a 2-day revision window — of
temperature, wholesale price, grid carbon intensity, observed underlying
demand, and observed wind and solar. Thirteen months are held so twelve
complete calendar months always exist for the falcon curve; every annual
figure is computed on the trailing 8,760 hours.

**What is modelled and what is measured.** Heat demand is modelled: annual
useful heat is distributed by daily heating-degree-day share, then within the
day by degree-hours, with hot water flat. Everything else — price, carbon,
demand, wind, solar — is measured. Days below 1 heating degree day carry no
space heat (ramping to full at 3†), because raw degree-hour allocation
otherwise assigns winter-strength heat to cool summer nights, which the weekly
estimator itself contradicts; weights are renormalised so the annual total is
unchanged.

**The three routes.** Each hour's replacement electricity is a Carnot fraction
with weather-compensated flow (30–50 °C) for space heat and a fixed 52 °C for
hot water†, sources being outdoor air less 3 K, ground at 8 °C, and networks
at 10 °C†. A single scale factor per route is solved so the year integrates
exactly to the published seasonal performance factors (2.80 / 3.24 / 5.0†).
The flow curve is the same for all three routes deliberately: the what-if
replaces heat in the existing building stock, so weather compensation applies
whatever the source. One consequence worth knowing: above about 11 °C,
air-source outperforms ground source, because outdoor air less its approach is
then warmer than the ground.

**Two different stress figures.** The *coldest hour* is the year's peak
modelled heat demand and reports each route's addition alone — gross, and
separated by the ratio of coefficients of performance. The *binding hour* is
the hour of greatest total call on dispatchable supply (observed demand plus
the net addition, less the wind and solar actually generated), and reports a
system total dominated by standing demand. Net subtracts the fifth of existing
resistive heating the what-if displaces; that credit is space-shaped, so
night-storage profiles would reduce it at peaks† and the gross figures bound
the zero-credit case. The two hours need not coincide.

**The electrification limit.** How far Britain could electrify its building
heat before the winter peak fills today's fleet: reported per route, against
the wind that actually blew and against a no-wind-all-winter counterfactual.
A **peak-capacity test, not an energy test** — all-of-it electrification via
networks would still need around 80 TWh a year of generation. Distribution
networks are unmodelled. One winter is one sample.

**The capacity line.** A fixed de-rated block of dispatchable and
interconnector capacity — 62 GW†, derived from the NESO Winter Outlook
2025/26 — plus the wind and solar actually generated. Renewables enter as
observed rather than de-rated, which removes the most contested assumption in
margin arithmetic and lets the line sag when the wind genuinely dropped. It is
a **static overlay, not a dispatch model**: no redispatch, imports response,
demand-side response or price response, no distribution constraints, and a GW
figure cannot represent storage duration. Exceedance is reported as hours
above available headroom under today's fleet, never as a claim about supply
interruption.

**Scope of the cooling figures.** Cooling here is the ECUK *buildings*
anchor — comfort cooling and ventilation. Data centres, cold store and the
wider cold chain sit outside it†, and would enter as their own series rather
than by inflating this one.

**Gates.** Four checks run before anything is published: the performance
anchors are reproduced; the hourly network route reconciles with the weekly
estimator (near-tautological by design — it proves wiring, not physics);
cooling is conserved against its annual; and any feed missing more than 5% of
its hours is refused. A failed gate leaves the previous hourly output in place
and the weekly panels untouched.

**Withdrawn in v8: the coincidence premium.** A statistic pricing each route's
electricity at the hour it is drawn against the flat mean — the cost of *when*
heat draws power rather than how much. Computed over a full year it was
diluted by summer hours in which almost no heat is drawn and went negative,
which is a contradiction on the page; restricted to the heating season it
recovered to a small positive figure for all three routes. It was withdrawn
anyway: hard to read at a glance, and not carrying weight in the argument. It
**still runs every day as an internal diagnostic**
(`coincidence_premium_diagnostic`, October–March basis) and is logged with
every build, so the question stays answered and the panel is restorable.

## The calibration gate is currently failing

Stated here because the site states it plainly. The trailing 12-month modelled
gas space-heat estimate runs about **10% above** the ECUK anchor, GB-adjusted
and weather-normalised — a ratio of ~1.10 against a publication gate of
|r − 1| ≤ 0.10. The page reports this openly rather than rescaling silently.

The step came from a correction, not a divergence. The anchor year's degree
days were being clipped by a fixed fetch window that had rolled past 1
January; restoring the full year raised the anchor-year HDD, which shrank the
weather-normalised anchor and exposed a gap the truncation had been flattering
into the gate. Candidate contributions to the residual: the treatment of
non-domestic gas, the fixed base temperature, and weather-normalisation
differences between the trailing window and the ECUK reference year. Under
review.

## Verification

The suites execute the renderers and estimators rather than parsing them:
`node --check` proves a file parses, not that a function survives its
arguments.

| Suite | What it guards |
|---|---|
| `test_page.js` | the page renders against a live payload |
| `test_bars.js` | the energy bars, live and windowed — including that a redraw *replaces* rather than appends |
| `test_capital.js` | the capital panel's published default, each lever's direction, and finiteness across the whole lever space |
| `test_front.js` | the six points, their evidence anchors, agreement with the capital panel, and that the frontispiece does **not** follow the trend selector |
| `test_premium.py` | the withdrawn premium's diagnostic, on its winter basis |
| `run_test.py`, `run_test_retro.py` | the weekly estimators and the hourly engine |

## Autumn maintenance — things that expire

| When | What | Why |
|---|---|---|
| October | **NESO Winter Outlook 2026/27** → update `DISPATCH_DERATED_GW` in `scripts/retro.py` and its dagger | The capacity line is a fixed block; last winter's fleet is not this winter's |
| Autumn | **ECUK 2026** re-anchor (heat-pump electricity, domestic shares, sectoral revisions) — also moves `ANCHOR_YEAR` 2024 → 2025 | Annual anchors lag 9–15 months |
| September | **Cooling reconciliation gate** — decide whether the year-round fit replaces the ECUK-anchored cooling estimate | Diagnostic published since v5.2; switchover would restate summer figures |
| Annual | **Capital panel class anchors** — Arup/DESNZ and the European comparator set | Unit capital moves with the drilling market |
| Quarterly | **Ofgem cap history** | Bill layer |

**Two version counters, not one.** `history_schema` tracks new *fields* on the
weekly history — a bump adds them and restates stored weeks so the new fields
appear. `anchor_epoch` tracks a changed *basis* — bump it whenever a constant
changes what a past week would have computed, and every recomputable stored
week is rewritten. A schema bump alone would leave the series half on one
basis and half on another.

## Method in one paragraph

Daily gas offtake to Britain's local distribution zones (LDZ — the network
serving homes and most businesses, excluding directly connected power
stations) is regressed against population-weighted GB heating degree days
(ERA5 reanalysis via Open-Meteo). The temperature-sensitive component is
attributed to space heating, following the published Watson et al. / Sansom
method; the trailing 12-month total is calibrated against the DESNZ ECUK
end-use tables, GB-adjusted and weather-normalised (current ratio ~1.10,
**outside** the ±10% publication threshold and reported as such). Other fuels
and cooling take their annual levels from ECUK 2025 (calendar 2024) shaped by
heating/cooling degree days. The indigenous share is measured on a services
basis — each unit of delivered heat or cooling inherits the UK-origin share of
its energy input, with harvested ambient/ground heat counting as 100%
indigenous, consistent with Eurostat/DUKES renewable-supply accounting.
**The full technical methodology — with equations, figures and a complete
bibliography — is in
[docs/uk-heatsplit-methodology.pdf](docs/uk-heatsplit-methodology.pdf).**

## Estimates open to challenge

Sourced figures (Ofgem cap, ECUK anchors, DESNZ GHG factors, Energy Systems
Catapult SPFs, EGEC capacity and market data, NISRA heating shares, MCS
installations, EHS/CCC overheating prevalence, ONS/UKHSA health and
productivity data, Arup/DESNZ and European unit-capital benchmarks) are cited
as such. Figures resting on Causeway judgement are marked † on the site — the
geothermal forecast scenario, several unit prices, the cooling split and
latent-demand extrapolation, the comfort-deficit stock fractions and
thermal-response coefficients, the UTES round-trip, indigenous input-origin
shares, the NI heat total, the WHY HEAT? service-level allocations, and every
input of the capital panel. Challenge and input welcome:
**contact@causewaygt.com**.

- **The hourly heat shape** is modelled from weather with no occupancy model;
  the summer damping threshold and the flow temperatures are conventions†,
  chosen to reconcile with the weekly estimator.
- **The capacity line** is one winter of observed weather under a fixed
  de-rated block†, and says nothing about storage duration or network
  constraints.
- **The displaced resistive credit** assumes the what-if samples the existing
  stock proportionally and that displaced load is space-shaped†; gross and net
  figures bound the truth between them.
- **The capital panel** is a calculator, not an estimate. Its class anchors
  are benchmarks from elsewhere applied to a UK deployment that does not yet
  exist at scale; the distribution and connection adders rest on a thin
  outturn sample; the network share splits evenly between ATES and doublets as
  a placeholder†; and build cost assumes the fleet exists from day one while
  savings accrue as it is built.

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
(underlying demand, embedded wind and solar) and Carbon Intensity API; NESO
Winter Outlook 2025/26 (the de-rated capacity block†). All open data, no
authentication.

Capital layer (v8): Arup for DESNZ, *UK Geothermal Review and Cost
Estimations* (DESNZ-ARP-REP-0003, Issue 05, 22 May 2025); European
ground-source, ATES, BTES and intermediate-doublet unit-capital comparators;
DECC/AECOM (2015) *Assessment of the Costs, Performance, and Characteristics
of UK Heat Networks* for distribution and connection benchmarks, escalated by
the RICS BCIS General Building Cost Index; the DESNZ heat network project
pipeline for scheme-level generation/distribution splits. Open Government
Licence v3.0 or cited.

## How it runs

A GitHub Actions cron (`.github/workflows/update.yml`) runs daily at 03:43
UTC: `scripts/build.py` pulls the feeds, fits the regressions, computes the
mix, costs, emissions, cooling tiers and headlines, and commits
`docs/data.json`; the static page (`docs/index.html`, Plotly) renders it. Feed
failures fall back to the last good values and are flagged on the page
(sources that publish on a lag show amber); build failures push a
notification. Site traffic is measured with GoatCounter (cookieless, no
personal data). No API keys required; fork-friendly.

The daily commit of `docs/data.json` also carries the weekly trend history
(capped at 60 weeks), so the git log doubles as its backup: each run appends
the newest complete week, recomputes the two most recent against feed
revisions, and leaves older weeks frozen as first published.

Anchor constants are refreshed on a maintenance calendar: Ofgem cap quarterly
(one row added to the cap-history table in `build.py`, so past weeks keep the
cap that was in force); ECUK/DUKES and the WHY HEAT? panel annually; the
geothermal panel annually on MCS/EGEC release; the capital panel's class
anchors annually.

## Repository layout

```
docs/
  index.html                      the dashboard (static; Plotly)
  data.json                       written daily by the workflow
  retro.json                      13-month hourly store, append-only (v7)
  uk-heatsplit-methodology.pdf    full technical methodology statement
scripts/
  build.py                        pulls feeds, fits, computes, writes data.json
  retro.py                        hourly engine: store, calibration, gates,
                                  slices, system view (v7)
  fetch_*.py                      per-source fetchers
test_page.js                      page renders against a live payload
test_bars.js                      energy bars, live and windowed
test_capital.js                   capital panel: default, levers, finiteness
test_front.js                     frontispiece: points, anchors, pinning
test_premium.py                   the withdrawn premium's diagnostic
run_test.py, run_test_retro.py    estimators and hourly engine
.github/workflows/update.yml      daily cron
```

## Versioning

The site carries a version (footer, `SITE_VERSION` in `docs/index.html`):
x.y.z where **x** = new data source or panel, **y** = update to an existing
source or anchor, **z** = wording or formatting. Current: **8.0.0**.

History: v1 launch (gas split, costs, spark gap, geothermal, NI) → v2 carbon
layer → v3.0–3.2 observed cooling analysis (NESO demand, response curve,
recency-aware sources) → v3.3–3.4 comfort deficit, tier graphic and UTES
dividend → v3.5 services-basis indigenous share and emissions headline → v3.6
EGEC Geothermal Market Report 2025 refinements → v4.0 WHY HEAT? whole-economy
panel → v4.0.1–4.0.3 methodology statement, carbon basis, residual SE and
calibration ratio surfaced → v5.0 live trend layer: weekly headline history
with per-week Ofgem cap and grid intensity, sparklines and what-if overlay →
v5.1 window selector re-totals the headline figures (12-month sums and
cumulative what-if saving) → v5.2 cooling reconciliation diagnostic
(year-round HDD+CDD regression, swept base) → v6.0 the empty bar (installed
hardware vs the 20% what-if, European comparators†; the sibling mirror
regression test from the July 2026 cross-calibration runs in the build checks,
panel withheld for now) → v6.1 share-of-national-heat calibration under the
empty bar → v6.2 indigenous share stored at one decimal (documented one-time
restatement of the weekly history) → v6.3 DUKES 2026 re-anchor (UK-origin gas
share 0.38→0.42) → v6.4–6.7 heat/cool splits — purchased energy, bill and
emissions, actuals and the 20% what-if alike, at every window from 1 week to
12 months (history schemas 3–6, restated by the same stored-CI mechanism) →
**v7.0 the hourly engine and the electricity grid impacts panel**: a 13-month
hourly retrospective (ERA5, Elexon, NESO), three replacement routes calibrated
to published SPFs, the coldest hour and the binding hour, a weather-breathing
capacity line from the NESO Winter Outlook, and the calendar-year falcon curve
with its cooling wing → v7.0.1 windowed energy bars (per-fuel block on every
stored week, history schema 7) → v7.1 the electrification limit, and the
anchor-year fix that moved the calibration ratio outside its gate →
**v8.0 the strategic case**: the site retitled UK Heat *Shift*, a six-figure
frontispiece computed from the panels below it, the capital panel answering
whether the saving pays for the build, a floating way back to either, and the
coincidence premium withdrawn to a logged diagnostic. Point 01's combustion
share is computed over the same twelve months as the figures beside it; the
capital panel carries a deployment period and a first-of-a-kind to
mature-supply-chain cost improvement.

*A Causeway Energies public-interest tool — https://causewaygt.com*
