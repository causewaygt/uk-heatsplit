# UK Heat Split — running build notes
Current release: v6.7.0 (live) · v7 in progress (Phase A' delivered,
branch workflow below). Sections are newest-first down to the original
v5.0.0 trend-layer handover, whose file-by-file record still applies.

## What changed, by file (v5.0.0 trend layer)

**scripts/build.py** (drop-in replacement)
- Weekly hero arithmetic refactored into compute_week() / compute_week_emissions(),
  used by both the live panels and the new history array — one estimator code path.
- build_history(): calendar weeks (Mon–Sun) where the gas feed served all 7 days.
  Entry: hero four + nested whatif twins + grid_ci + cap_from. First run backfills
  the whole gas window; thereafter appends the newest complete week, recomputes the
  2 most recent (feed revisions), freezes older entries, caps at HISTORY_MAX=60.
  Weeks whose CI fetch fails are skipped and retried next run (3 consecutive
  failures abort the pass, keeping what was built).
- CAP_HISTORY table + cap_prices(date): live panels AND history priced from the
  table. Quarterly cap update = add one row; past weeks keep their cap.
- fetch_ci_weekly_mean(): NESO CI API, mean of half-hourly actuals for the week
  (forecast fallback, rejects <240 values).
- out["gas_window"] = {oldest, newest, days_served} every run — the empirical
  live/modelled boundary. Check it in the first Actions log.
- History carried forward at the top of main() so error-path writes cannot wipe it.
- out["history_note"] documents the series' construction and limits (surfaced on
  the site in Method & caveats).

**docs/index.html** (v4.0.3 → v5.0.0)
- Trend layer inlined — no separate JS/CSS files; single-file structure kept.
  Internally namespaced "trend"/"tr-" to avoid the existing #ticker spark-gap box.
- Per hero stat: sparkline (actual solid paper, what-if green, gap band shaded),
  delta vs window start (pp for indig_pct, % otherwise; green/ember by whether
  the move is welcome, not by sign), last-point dot.
- One shared window selector 1w/4w/12w/12m (2/5/13/53 points), default 12w,
  aria-pressed, debounced resize redraw, preserveAspectRatio + non-scaling
  strokes.
- Layer hides entirely (selector included) when history is absent or <2 entries;
  page renders normally without it.
- Method & caveats: "Trends." paragraph filled from history_note + gas_window.
- Dashed strokes deliberately NOT used — reserved for the phase-2 modelled
  grammar.

**README.md**
- Trends bullet under "What the dashboard shows"; history persistence + quarterly
  cap-table edit under "How it runs"; version history reconciled (4.0.1–4.0.3)
  and extended with v5.0; current 5.0.0.

**uk-heatsplit-methodology.docx / .pdf** (v4.0.0 → v5.0.0)
- Rebuilt as an editable Writer-compatible .docx (original source was LibreOffice;
  you now have a maintainable source) + PDF export, 9 pages, figures 1–4 carried
  over from the v4 PDF.
- New Section 9 "Historical series (the trend layer)": 9.1 construction,
  9.2 time-consistent pricing/carbon and its stated limits, 9.3 the empirical
  live/modelled boundary + planned distinct-grammar back-cast, 9.4 presentation.
- Automation renumbered → 10 (adds commit-log-as-backup note); limitations → 11
  (adds trend-layer bullet). Abstract gains one sentence. References unchanged.
- Drop into docs/ as uk-heatsplit-methodology.pdf (site + README link to that
  path).

## Sector-blended bill (added after review)
- Gas and electricity in the national bill are now priced by sector: each
  component's ECUK U3/U5 domestic share at the Ofgem cap, the services
  remainder at NONDOM_PRICES_P_PER_KWH (gas 5.5 / elec 24.0 p/kWh incl CCL,
  Causeway estimates † anchored on DESNZ QEP tables 3.4.1-3.4.2; Q4 2025
  manufacturing 3.5/16.8 excl CCL is the large-user floor). Cooling (ECUK
  services-only) is priced wholly non-domestic. What-if replacement
  electricity blends at the delivered-heat domestic share (0.73 †).
- Effect on stub data: weekly bill 271 -> 255 £m (~-6%); cooling line most
  affected. History inherits the blend via compute_week.
- ANNUAL maintenance: refresh NONDOM_PRICES from the QEP non-domestic
  size-band xlsx (gov.uk statistical-data-sets/gas-and-electricity-prices-
  in-the-non-domestic-sector) with the ECUK update. DOM_SHARE moves only
  when ANNUAL_TWH does. Refine the two estimates from the small/medium
  bands in that xlsx when convenient - I could not pull the file into the
  sandbox, so 5.5/24.0 are narrative-anchored, not table-read.
- Household p-per-useful-kWh route table, spark gap, and carbon are
  untouched (explicitly domestic / wholesale / physical respectively).
- run_test.py now pins DOM_SHARE to 1.0 for the equivalence check and
  asserts blend direction separately; exits nonzero on failure.

## Windowed headline totals (v5.1.0)
- The 1w/4w/12w/12m selector now re-totals the hero figures, actuals and
  what-if rows alike: sums over the last 4/12/52 complete live weeks
  (auto-scaled to TWh / £bn / Mt), indigenous share as a purchased-energy-
  weighted average, what-if bill and emissions captioned with the
  cumulative saving over the window.
- Default is 1w = this week: hero shows the live estimates exactly as
  before (snapshot/restore, byte-identical). The sparkline always matches
  the selected window (v5.2.1 - the earlier full-record-at-1w behaviour
  read as a bug); at 1w it is a two-point segment, delta vs last week.
- Heat/cool splits in the bill and emissions captions exist only for the
  live week (history doesn't carry them), so windowed captions drop them.
- If the window has fewer complete weeks than requested, the caption says
  so: "over the last 12 months (37 live wks)".

## Cooling reconciliation diagnostic (v5.2.0)
- build.py: year-round OLS (pure python, no new deps) of daily underlying
  electricity demand on HDD + CDD jointly; CDD base swept 10/12/14/16/18,
  HDD base over the feed's bases, selected on R2. Weekend (Sat, Sun),
  August and Dec20-Jan3 dummies, linear trend. Outputs
  out["cooling_reconciliation"]: chosen bases, slopes with t-stat, R2,
  resid SE, annual + this-week weather-driven cooling electricity
  (slope x CDD at the CHOSEN base), ECUK anchor, implied weather-flat
  share, and the summer centring slope for cross-checking. DIAGNOSTIC
  ONLY - nothing feeds the bill or carbon chain; error path carries the
  previous value forward.
- Daily mean temp is reconstructed from the dd feed (highest HDD base /
  CDD18 / dead-band midpoint). If your real fetch_degree_days exposes raw
  temperatures, swap them in at the marked comment - it removes the
  +-0.5 degC dead-band approximation.
- index.html: diagnostic strip in the cooling section between the observed
  curve and tier 3; hides when the key is absent.
- Stub validation: stubs now share one deterministic per-date temperature
  (stubs/stub_weather.py) so gas, electricity and degree days respond to
  the same weather, as the real feeds do - previously each stub drew
  independent noise, which attenuated any cross-feed regression. With
  truth = 25 GWh/CDD(18) saturating at 2.5 CDD, noise sd 15: recovered
  base 18.0 exactly, slope 20.7 (attenuation from the injected saturation
  is expected), heat term 0.0 (truth none), t 20.4, R2 0.814, resid SE 15
  = injected noise. Summer centring slope 22.1 agrees.
- Interpreting production output: expect a LOW chosen base (12-16) - real
  commercial cooling starts well below 18 - and expect the weather-flat
  share to land high (0.9+). The flat remainder is an upper bound on
  ventilation + base-load cooling + below-base conditioning; it cannot be
  split further from demand data alone. Decision on switching the
  bill/carbon chain to observed cooling stays manual, after a full season
  side by side.

## Stability log (v5.3.0)
- cooling_reconciliation gains stability_log: one entry per run-date
  (fitted base, slope, t, R2, n_days, centring slope for the agreement
  check), idempotent by date, capped at 120, carried forward on error
  with the rest of the dict. A stability summary reports the 60-day
  base and slope ranges against the switchover gate (base within +-1C,
  slope t>5, estimators within ~1.5x, holdout comparable).
- Site: when >=2 runs are logged, the diagnostic note appends
  "Stability (N runs, 60d): fitted base X-Y degC, slope A-B GWh/CDD -
  within / not yet within the switchover gate".
- The gate check for the October decision reads straight off this key.

## Sibling cross-calibration (data + test only; panel withheld)
- build.py still emits out["sibling"] (UK per-capita heat input vs the
  island's reference constants, ratio, 0.8-1.2 gate, conventions and
  cooling caveat) and run_test carries
  test_irish_sibling_ratio_regression per the July 2026 exchange - the
  mirror discipline runs on every build.
- The DISPLAY panel was built and then withheld at Simon's request; no
  markup or renderer remains in index.html. To reinstate, the panel is
  in the git history of this working session (v6.0.0-era index.html) or
  I can rebuild it against out["sibling"] unchanged.

## The empty bar (v6.1.0)
- UK version of the Irish sibling's hardware comparator, inside the
  geothermal section. build.py adds geothermal["hardware"]: UK installed
  (gshp 850 MWth † EGEC 2025, deep ~10 MWth †), the what-if requirement
  computed LIVE (20% x delivered buildings heat at 2,000 EFLH † - moves
  with the model; stub ~30.4 GWth, real data ~35-37), per-person watts
  (UK ~13 vs Sweden 778 †), 2025 sales, and comparator constants shared
  with the Irish data (France 2,250+767, Netherlands 2,500+353, Sweden
  8,000+167 MWth - SPLITS APPROXIMATE †, sync from Irish data.json if
  the sibling re-anchors).
- Front-end: three stats + stacked Plotly bars (green installed, blue
  deeper, hatched what-if remainder - Plotly 2.32 pattern fill) with the
  what-if annotation; hides when the key is absent.
- The headline visual: even Sweden's fleet covers ~1/4 of the UK
  requirement; the per-person framing (778 vs 13 W) is the counter to
  "too big to be real".

## Share-of-national-heat calibration (v6.1.0)
- Answers "why is the what-if bigger than Sweden": it isn't, per system
  size. hardware.share_of_national_heat: each fleet at 2,000 EFLH as %
  of its own buildings heat. National anchors † (input basis): UK live
  from the model (~430 TWh), France 350, Netherlands 115, Sweden 80
  (IEA / ODYSSEE / Heat Roadmap order). Result: UK 0.5%, FR ~2%, NL ~5%,
  SE ~20% - Sweden's fleet already serves the what-if share.
- Second chart under the empty bar: share bars with a 20% reference
  line; countries at/above the line render in green.
- Boiler-flow framing in the note: ~1.6m UK boilers replaced/yr ≈ 35-40
  GW combustion capacity installed annually † - the what-if ≈ one year
  of replacement flow. (Installed-capacity national statistics for the
  four countries do not exist; demand-share is the sourced proxy.)
- Delivered-vs-input mismatch (~15-20%) accepted and stated at this
  precision. Cooling capacity excluded per the standing scope caveat.

## Base-sweep fix (post-v6.1 build.py patch)
- The reconciliation diagnostic's CDD base sweep carried a bc > bh
  constraint that, against the real feed's 14.5-16.5C heating bases,
  silently reduced the documented 10-18C sweep to {16, 18}. The five
  "stable at 18.0" production runs to 27 July tested only that pair.
  Constraint removed: cooling below the heating base is different
  buildings on the same day in a mixed stock; the joint fit separates
  the terms at the kink points and the t-stat gate guards degeneracy.
- Consequence: the fitted base may move on the first post-fix run, and
  the stability clock effectively restarts - treat base_range_60d as
  meaningful only from the fix date. September gate timing unaffected.
- Stub validation unchanged: truth base 18.0 still recovered exactly
  from the full 15-candidate sweep.

## Comparator verification (post-v6.1)
- Simon challenged whether France/NL "ground source" was really GSHP.
  Verified against the EGC 2025 country update summary (Sanner et al.,
  Tables 3-4, end-2024) - the Irish totals resolve to exactly GSHP +
  deep direct use: France 2,293+724 = 3,017; Netherlands 2,486+367 =
  2,853; Sweden 8,120+47 = 8,167. Constants now pinned to the published
  values (previously my approximate splits); "approximate" caveat
  removed. UK gshp updated 850 -> 861 MWth (same table).
- Genuine caveat the challenge surfaced, now stated on panel and in
  methodology: the Netherlands' DEEP 367 MWth is almost entirely
  greenhouses (agri 363, DH 3.8) - not buildings heat. NL GSHP is real
  (163k units, 3rd-largest EU sales market; low 1,095 full-load hours
  reflect ATES-style balanced use).
- sweden_per_person 778 -> 773 W (8,120/10.5m). Shares unchanged
  (totals identical).

## What-if SCOP framing (v6.1.1, wording only)
- indig_note now states the premise: SCOP 5 is a blended estimate across
  depths and system types, INCLUDING seasonally balanced / UTES-coupled
  networks - storage and recovery are factored in, not additional. This
  is the agreed public position (LinkedIn seasonal-storage exchange, 27
  July); never stack a separate storage credit on the headline saving.
- OPEN CROSS-CAL ITEM: the Irish site's what-if SCOP and framing are not
  visible in its page (wf_ values precomputed in its data.json). Check
  the Irish build script's equivalent constant; sync value + framing or
  record the deliberate difference in both methodologies and add the
  what-if parameters to the cross-calibration conventions list.

## Patch 0 (v6.2.0): indigenous share at one decimal + restatement
- WHY: history stored indig_pct as an integer; winter months quantised to
  one flat value on the 12m sparkline (the artifact Simon spotted). The
  physics is also real - gas-dominated winter weeks genuinely pin the
  services blend - but the decimal reveals the true small variation.
- compute_week services shares now 1 dp; hero display stays integer
  (57%); sparkline pp deltas render one decimal when fractional.
- history_schema = 2: on the first run after deploy, every stored week
  still inside the live gas window is restated to 1 dp, reusing its
  STORED grid_ci (no refetch storm - still exactly 2 CI calls) and the
  cap in force per week; all other fields re-round identically. Weeks
  the gas window has rolled past keep integer values (unrecomputable).
- DEPLOY URGENCY: the gas window's oldest served date is days from
  overtaking the first history week (2025-06-22). Deploy build.py and
  run the Action BEFORE that happens or the earliest weeks stay
  quantised permanently. index.html (v6.2.0) can follow at leisure.
- One-time restatement documented under the sector-blend precedent.

## Patch 1 (v6.3.0): DUKES 2026 re-anchor
- INDIG gas 0.38 -> 0.42. DUKES 2026 (2025 data) supply pool: UKCS
  production 332.1 + UK biomethane 5.8 over gross supply 810.2 TWh
  (incl imports 463.7, stock draw 8.6) = 41.7%. Proportional-draw
  basis, matching DUKES's own framing (Norway = 40% of gross supply).
  Raises the hero indigenous share ~2pp; applied uniformly across
  history per the section-9.2 convention (documented restatement).
- HP_ELEC_TWH deliberately UNCHANGED at 2.0: DUKES 2026 ambient heat
  (1,386 ktoe = 16.1 TWh) counts reversible air-con heating across the
  non-domestic fleet - different basis from the model's hydronic-
  domestic subset. Reconcile at the ECUK 2026 re-anchor, not before.
  Basis note now in the code comment.
- MCM_TO_GWH / calorific value: constant lives in fetch_gas.py (not in
  this repo copy); annex A not to hand. SIMON: one-line check of the
  conversion against DUKES 2026 annex A CV when convenient.
- For the autumn ECUK re-anchor: DUKES 2026 revises sectoral gas
  1998-2024 (industry<->services reallocation - touches DOM_SHARE
  provenance); services gas fell 7.6% in 2025 (calibration-ratio
  residual context). Prose refs updated site-wide; methodology ref
  [22] now DUKES 2026.

## Heat/cool split in the hero energy caption (v6.4.0)
- 1w: live split from headlines (heat_GWh / cooling_GWh, v6.3.1).
- 4w/12w/12m: history schema 3 stores the per-week split; on first run
  after deploy the schema restatement recomputes all in-window weeks
  (stored CI reused, no fetch storm - same proven mechanism as the
  decimal restatement) and the windowed captions then show summed
  splits, TWh-scaled when large. Week w/e 2025-06-22 remains outside
  the gas window: it gains no split, so the 12m caption omits the
  split until that week rolls off (~mid-August, 60-week cap) - the
  4w and 12w windows show it from day one.

## What-if bill + emissions splits (v6.7.0)
- The green strip now carries heat/cool splits on ALL THREE splittable
  figures at 1w and every window, keeping the saves: "national bill
  (heat GBPx · cooling GBPy · saves GBPz over the last 12 months)";
  emissions likewise. What-if cooling bill = kept cooling electricity
  + near-passive replacement at the cooling blend rate; emissions =
  same volumes at the week's CI; heat = remainder in each case.
- History schema 6 (fifth restatement, same stored-CI mechanism);
  headlines whatif + carbon block gain the split fields. Same oldest-
  weeks fallback as before; complete 12m captions ~late August.
- The hero grid is now fully symmetric: actuals and what-if, four
  windows, three splits each. No further split candidates exist -
  indigenous share is a ratio.

## Bill + emissions heat/cool splits in windows (v6.6.0)
- 4w/12w/12m bill caption: "national bill over the last 12 months
  (heat GBPx · cooling GBPy)"; emissions likewise (kt/Mt scaled).
  History schema 5 stores bill_heat/cool_Mgbp and emissions_heat/
  cool_kt per week; the restatement backfills in-window weeks (stored
  CI, same mechanism, fourth use). Same fallback: captions omit the
  split while the un-restatable oldest weeks remain in the 12m window
  (gone ~late August). 1w already carried both splits live.

## What-if heat/cool split (v6.5.0)
- The green strip's purchased caption now carries the same split as the
  actuals row, at 1w and all windows: what-if cooling = kept cooling
  electricity + its near-passive replacement; what-if heat = the rest.
- History schema 4: the restatement adds the whatif split to all
  in-window weeks (stored CI reused, same mechanism). By deploy the two
  oldest weeks (w/e 2025-06-22 and possibly 06-29) will sit outside the
  gas window and stay split-less; the 12m what-if caption omits its
  split until they roll off (~late August), 4w/12w complete from day
  one - same fallback behaviour as the actuals split.

## v7 working arrangement - branch, artefact discipline, cutover
DECISION (1 Aug): v7 is developed on a `v7` branch of the live repo
(optionally via a private fork pushing to it), NOT a separate repo
renamed at launch. Pages serves only main:/docs, so the branch is
invisible to the live site by construction; cutover is a reviewed
merge commit, not a repo swap with its 404 window, Pages re-provision,
Actions re-verification and stale-history copy.

ARTEFACT DISCIPLINE (the one rule that prevents a bad merge):
- docs/data.json and docs/retro.json are MAIN-OWNED build artefacts.
  The v7 branch NEVER commits its development copies of them. Add on
  the branch:
      .gitattributes:  docs/data.json merge=ours
                       docs/retro.json merge=ours
  ("ours" = main's copy wins at merge; enable once with
   `git config merge.ours.driver true`) - belt and braces on top of
  simply not committing them.
- Anything else (build.py, retro.py, index.html, stubs, tests, docs)
  merges normally.

PRE-CUTOVER, ON THE BRANCH:
- [ ] run_test.py + run_test_retro.py + test_page.js green
- [ ] retro backfill via workflow_dispatch on the branch -> retro.json
      arrives warm; commit it to the branch ONLY as a dispatch artefact
      check, not merged (main's first run rebuilds/extends it)
- [ ] SYNC flags resolved: fetch_degree_days coords/weights + live
      HDD base into retro.py
- [ ] copy deck (Phase D) signed off by Simon

CUTOVER DAY (same-day rule, learned from the restatements x3):
- [ ] merge v7 -> main in the MORNING, UK time
- [ ] trigger the Action immediately (workflow_dispatch)
- [ ] verify log: eta triple + both gate verdicts printed; any v7
      schema restatement caught while the gas window still serves
- [ ] click the page: version string, H/D/M toggle, collapsed panels
- [ ] if the log shows gate failure: retro slices refuse to render by
      design - the weekly site is unaffected; fix forward on main
- [ ] tag the pre-merge main commit (rollback = revert the merge)

## Phase B addendum - the cooling hump (summer grid stress)
- Cooling built into the retrospective on the PRODUCTION convention
  (mirrored exactly): ECUK annual x GB share, half flat / half shaped
  by cooling degree-hours at the production base 18.0; EER 3.0 useful
  multiple; what-if relief = R x (elec - useful/COP20). Series DERIVED
  at slice time from stored temps - retro.json schema unchanged, params
  stored under calibration.cooling for provenance. When the September
  reconciliation gate lands a new split/base, the retro inherits it by
  changing the passed params only.
- ACCOUNTING GUARD (the one fatal error avoided): cooling is EMBEDDED
  in observed demand; heat routes are ADDED load. Every summer output
  says so. stress_summer: peak cooling hour (argmax), today_GW
  (embedded dagger), x2_scenario_added_GW (the on-record doubling
  prediction, labelled as prediction), whatif_relief_GW (negative
  delta: 20% near-passive REMOVES this at the same hour), same-day
  ceiling. Monthly rows gain cool_GWh + cool_whatif_relief_GWh; hourly
  windows carry the cooling trace.
- Stub validation: annual 10,244 GWh conserved through the shaping;
  Jul cool 2,056 vs Jan 435 GWh (the second hump); peak 13.9 GW
  embedded / 2.4 GW removable; both suites green, equivalence clean.
- Viz updated: summer stress cards, amber cooling traces in both
  hourly windows, falcon now two-humped with the doubled-scenario
  dotted line. Standalone rebuilt (plotly inlined + blob-URL shim -
  the shim goes into Phase C's real panel too).

## v7.1 DESIGN OF RECORD - the system view (frozen 1 Aug, build in Aug)
STRATEGIC FRAME (Simon): this panel, delivered with the layers below,
is the candidate deal-clincher in Heat Split's strategic case for
geothermal in the UK. Timeline: August build, September peer review,
October conference presentation. Single cutover, late August, complete
- v7.0 does not ship separately.

THE THREE LAYERS
1. OBSERVED HOURLY DEMAND (new feed): Elexon INDO / NESO historic
   half-hourly demand incl embedded wind+solar estimates (the same
   file the cooling estimator uses daily). 13 months, hourly means.
   Chart base layer; retires the daily-average stress basis - cards
   move to "% of that HOUR's demand" and "% of the record hour".
2. ROUTE ADDITIONS STACKED ON IT, with the netting refinement: the
   what-if displaces a fifth of existing resistive heating already
   inside observed demand - net it off the addition (currently
   overstated by ignoring it). Touches G2; re-verify.
3. THE CEILING - capacity to deliver, breathing with weather:
   dispatchable de-rated block (seasonal constant, SOURCED - decision
   needed: NESO Winter Outlook de-rated margin vs DUKES 5.7; whole
   credibility of "exceeds headroom" hangs on the choice and its
   statement; search current Winter Outlook before coding, dagger
   regardless) + OBSERVED hourly wind (FUELINST transmission +
   embedded from feed above) + observed solar. Ceiling sags on still
   cold nights exactly when the ashp stack peaks - the point.

CLAIM DISCIPLINE (engine-assembled basis text, non-negotiable):
- "hours where the addition exceeds available headroom under today's
  fleet (dagger)" - NEVER "blackout hours"; static overlay, not a
  dispatch model (no redispatch/imports/price response modelled).
- Transmission-level; distribution constraints unmodelled, stated.
- GW ceiling cannot represent storage DURATION - batteries serve the
  peak hour, not the fifth cold day; the worst-week exhibit shows the
  multi-day draw that duration-limited storage cannot cover. State it
  - it is the geothermal argument's friend.
- Dunkelflaute check the moment wind data lands: was w/e 2026-01-06
  still as well as cold? If yes, the exhibit shows the ashp fifth
  squeezing into a sagging ceiling - the strategic case at system
  level in one picture; also explains the coincidence premium.

MECHANICS: retro.json schema 2 (three new hourly arrays: demand_GW,
wind_GW, solar_GW; same restatement discipline, warm store rebuilt
once); panel gains a fourth toggle view "System"; stress block
re-based; copy deck stress rows re-drafted on the hourly basis before
Simon's edit (do not polish the 43% wording twice - it changes shape
entirely on an hourly denominator).

BUILD ORDER (corrected to the original strategy's layering - these
are the A'/B/C phases run again for the second data family, with the
proven discipline applied wholesale: stub-first validation with
injected truth, gap-refusal, schema restatement, gates before render,
carry on failure):
  AUDIT PASS (precedes all new code, per Simon 2 Aug): a written
    audit memo on the EXISTING retro.py + build.py retro block +
    harnesses before anything new lands. Scope: constants provenance
    and dagger inventory (every number traced to source or marked);
    basis strings vs actual computation (no drift); error/carry paths
    exercised; schema+restatement correctness; the known netting
    overstatement quantified; duplicated logic; fetcher timeouts and
    failure modes; dead code. Findings fixed or explicitly accepted
    before A'.2 opens.
  AUDIT COMPLETE (2 Aug): memo delivered (uk-heatsplit-audit-memo
    .docx). 12 findings: 6 fixed (incl a syntactically broken MID
    block from an unverified session-boundary edit - caught here, not
    in production; zip builds now syntax-gated), 3 accepted with
    statement, 3 deferred to named phases (netting -> B.2 quantified
    at ~1.6 GW worst-hour, 10-22% of stated additions; COP-param
    centralisation + ERA5 recency check -> A'.2; premium-note wording
    -> D). Store schema governs restatement; slices schema versions
    the render contract. A'.2 CLEARED TO OPEN.
  A'.2 hourly demand + wind + solar fetchers (Elexon INDO / NESO
    historic HH file / FUELINST), stub twins in fetch_hourly with
    injected truth, retro.json schema 2, warm-store rebuild via
    branch dispatch.
  A'.2 COMPLETE (2 Aug): retro.json schema 2. Real fetchers:
    NESO historic-demand-data via CKAN package_show DISCOVERY (no
    hardcoded resource ids - they churn yearly) -> ND + embedded
    wind/solar, half-hourly, SQL-filtered per year-file; Elexon
    FUELINST (fuelType=WIND) chunked+retried -> transmission wind.
    demand_GW = ND + embedded wind + embedded solar (UNDERLYING-demand
    convention, embedded on both sides of the ledger - basis to be
    stated on-panel in C.2). wind_GW = transmission + embedded.
    VERIFY-ON-DISPATCH: field names vs the published demanddata
    schema; first branch run confirms or corrects (the MID lesson).
    Upgrade path: schema-1 store keeps its four arrays frozen; system
    arrays fetched FULL-SPAN once (they cannot be restated from stored
    inputs), incremental thereafter; trim covers all seven arrays.
    A6 shipped: calibration carries flow_at + source approaches so the
    panel derives physics from data, no JS hardcodes (C.2 consumes).
    Stub twins carry injected truths - cold-still wind correlation
    (stub Dunkelflautes by construction), dark-night solar, winter
    demand - all asserted end-to-end through the store; upgrade test
    proves one-shot full-span fetch + frozen-array preservation.
    A8 note: ERA5 recency check deferred to the first schema-2
    dispatch (count trailing filled hours in the log).
  B.2 stress re-based to hourly-observed denominators; resistive
    netting; ceiling construction (dispatchable de-rated block from
    the NESO Winter Outlook 2025/26 workbook (dagger) + observed
    renewables - the construction that sidesteps the de-rated-wind
    critique); headroom-exceedance slices; gates re-verified incl
    netting's touch on G2; Dunkelflaute check on w/e 2026-01-06.
  B.2 COMPLETE (2 Aug): netting + hourly re-base + ceiling + binding
    hour. _displaced_gw: the fifth of existing resistive space heating
    (input from build: elec_space - HP_ELEC = 19.2 TWh), shaped like
    space heat, route-independent; net = gross - displaced everywhere
    in the System view; G2 stays on GROSS (the site's weekly what-if
    is gross - basis match, not oversight). Stress cards re-based to
    OBSERVED HOURLY denominators (pct_of_hour, record hour + its
    timestamp); daily-average fields retained only as fallback for a
    schema-1 store; summer card gains today_pct_of_hour. Windows carry
    demand/wind/solar arrays for C.2. slices schema 2 with "system":
    per-route binding hour (argmax D + net - W - S over Nov-Mar,
    trailing year), dispatch requirement, headroom vs the 62 GW
    block (dagger - WO 2025/26 arithmetic in the constant's comment),
    hours_above_block exceedance counts, record observed hour, full
    claim-discipline basis string (static overlay, no dispatch model,
    duration point, one-winter caveat with the windy-cold/mild-still
    facts). Real-data preview from the banked store: binding hour =
    Jan 5 08:00, ashp req 46.5 GW vs network 38.8 - the ~8 GW spread.
    Stub harness: netting bites, identity holds, winter-only binding,
    exceedance ordering, windows carry system arrays. Build log gains
    the binding line.
  C.2 System view on the existing toggle; stress cards re-worded to
    the hourly basis; ceiling caveats in the engine-assembled footer.
  D resumed ONCE, after all evidence layers exist: folds done and
    keep; copy deck re-cut against the complete panel for a single
    edit pass.
  E docs + conventions + README; cutover late August.
FRAMING (Simon endorses, for the strategic case): v6 DESCRIBES the
heat transition; v7 TESTS it against the grid Britain actually has,
hour by hour, weather included.

## Phase D - collapse the evidence + copy deck (branch)
- Rename shipped with it: "SCOP-5 ambient networks" -> "SCOP-5
  geothermal networks" (panel lede + trace/card label + retro.py
  docstring).
- FOLDS (details.fold, closed by default, + / - markers, conclusion
  stays visible above each): fold_engine (the whole gas engine room
  under "UNDER THE BONNET"), fold_cool (observed response curve +
  reconciliation diagnostic; the coolobsline conclusion stays out),
  fold_cost (route price table; billline spark conclusion stays out),
  fold_carb (route carbon table; carbline stays out). Page tests
  assert folds exist, are closed, engine content is inside, and
  conclusions are outside.
- Kept open by design: hero + what-if strip, routes panel, dual bars,
  sparklines, empty bar + shares, tier-3 comfort deficit + UTES, NI,
  WHY HEAT.
- COPY DECK delivered for Simon's edit (uk-heatsplit-copy-deck.docx):
  current vs proposed for the panels' user-facing text, including the
  summer-43% caveat clause and the cold-economy scope clause, both
  awaiting his sign-off before Phase E freezes the wording.

## Phase C - the three-routes panel (v7.0.0, branch)
- New section id="routes" in the gas engine's slot (engine stays visible
  until Phase D collapses it), hidden by default and revealed only when
  data.whatif_routes exists - graceful absence proven (retro failure or
  pre-retro data.json leaves the page exactly as v6.7).
- Layout as user-approved in the validation view: winter stress cards ->
  summer stress cards (embedded-basis wording carried into the cards
  themselves) -> H/D/M toggle chart (Hourly-7d live / Daily-90 /
  Monthly-13 falcon with both wings) -> frozen worst-week exhibit ->
  COP curves (client-derived from calibration: eta + flow + defrost,
  never stored) -> premium cards -> basis note concatenating the
  engine's own basis strings.
- Dashed grammar's reserved first use: modelled heat + embedded cooling
  dashed; observed-price/carbon untouched. Toggle reuses the trendctl
  pattern (aria-pressed, Plotly.react).
- HERO FIXES shipped alongside: 1w what-if captions gain saves ("saves
  GBP32m this week" / "saves 335 GWh this week"); dfmt stamps the year
  on any delta reference older than 300 days ("vs 27 Jul 25").
- SITE_VERSION 7.0.0. Page suite extended (panel cards, saves regexes
  in the 1w context, absence case); Plotly harness stubs gain react().
  RENDERER SCOPE TRAP banked: the main script is one fetch().then(d=>)
  callback - anything appended after its close sees no d; insert
  before the .catch anchor.
- Phase D remaining: collapse-the-evidence layout + copy deck; then E.

## Phase B addendum - the cooling layer (falcon's second wing)
- retro gains embedded cooling: build.py passes the PRODUCTION
  convention verbatim (ANNUAL_TWH cooling_vent, 50% flat / 50% shaped
  by cooling degree-hours at the live COOL_BASE, EER 3.0 on the shaped
  half, passive COP 20) - NOT the reconciliation diagnostic's fit,
  which stays gated until September; when the October switchover lands
  the retro inherits it via the parameters, same discipline as the
  heating base.
- ACCOUNTING RULE, stated everywhere shown: cooling is EMBEDDED in
  observed demand - the opposite of the heat routes' ADDED load; never
  mix silently. stress_summer block: peak cooling hour empirically
  located, today_GW (embedded), x2_scenario_added_GW (the on-record
  dagger prediction as uniform doubling), whatif_relief_GW (20% moved
  near-passive REMOVES this at the same hour = the double-dividend:
  the heat what-if limits the winter peak's growth, the cooling
  what-if subtracts from the summer one).
- Windows and monthly aggregates carry cool_GWh (+ per-agg what-if
  relief); harness asserts conservation to the annual, the summer
  hump (Jul > 2x Jan), relief bounds, and array shapes. Viz: summer
  stress card row + the falcon's second wing (embedded dashed, doubled
  dotted).
- Stub validation: summer peak 2026-07-03T15 at 13.9 GW embedded,
  relief 2.4 GW; annual 10,244 GWh conserved exactly.

## Phase B - slice and stress (v7, branch)
- retro.py gains: trim (400-day store cap), _route_base (precomputed
  Carnot bases - calibration and integration ~0.3s), slices() emitting
  everything Phase C renders: hourly_live_7d, worst_week (rolling-168h
  max, empirically located), daily_90, monthly_13 (falcon-curve
  aggregation), stress block, coincidence_premium. save/load paths
  late-bound so tests can redirect the store.
- build.py: retro block at the END of main() (NOT in _write - prev is
  main-scoped; learned the hard way): computes useful space/DHW annuals
  from the model's own constants, G2 figure = (space+dhw) x 0.20 / 5.0
  (near-tautological by design - proves wiring and heat conservation),
  passes the LIVE regression base, runs gates, saves the store, emits
  out["whatif_routes"], prints gates/eta/stress/premium lines; on ANY
  failure carries prev's whatif_routes (weekly site never blocked).
  RETRO_FETCHERS module hook for test injection.
- Stress ceilings basis: DAILY-AVERAGE observed demand (same-day and
  record-day GW from the underlying-demand series) - hourly observed
  demand is not a feed here; stated in the block. Hours-above-threshold
  distribution per route (5-25 GW).
- Coincidence premium: hourly-priced vs flat-mean-priced annual cost
  per route. Stubs show ashp > network ordering at ~3% vs ~2.8%; real
  GB cold-evening/price coincidence should spread these materially.
- VALIDATED: run_test_retro Phase B section (slices shapes, worst hour
  == argmax, stress ordering a>s>n, monotone hours-above, premium
  ordering, trim, falcon monthly shape) + full weekly suite green with
  equivalence IDENTICAL (whatif_routes stripped as additive).
- TEST-HARNESS TRAPS BANKED: (1) every importlib.reload(build) resets
  RETRO_FETCHERS - rewire after EVERY reload or the retro block silently
  attempts real network and hangs in the retry ladder (found via
  faulthandler.dump_traceback_later); (2) background processes do not
  survive across sandbox tool calls; (3) substring-replace on "import
  build" also hits "import build_orig".
- Stub diurnal sign fixed (warmest 15:00); synthetic worst hour now
  pre-dawn as physics expects.

## Phase A' - 12-month hourly retrospective engine (v7 foundation)
- NEW scripts/retro.py, standalone module (build.py untouched - Phase B
  wires it in). Fetchers: ERA5 hourly via Open-Meteo archive - GB_POINTS
  copied VERBATIM from the live fetch_degree_days.py (SYNC RESOLVED
  1 Aug; keep the two lists in lockstep), one batched request with
  retries per that module's pattern, <90%-weight hours left to the gap
  policy; Elexon MID half-hourly -> hourly; NESO CI chunked hourly.
  Gap policy: fill isolated holes, refuse >5%. The within-day shaping
  base is a build_retro PARAMETER (base_c) - Phase B passes the live
  regression's selected base (currently 16.5), never a hardcode.
- Heat shape MODELLED (dagger): annual useful space heat by daily HDD
  share, within-day by heating degree-hours (base 15.5 - SYNC with the
  live regression base); DHW flat. Price + carbon OBSERVED hourly.
- Carnot COP, weather-compensated flow 30-50C; sources: air -3C
  approach / ground 8C / network 10C; ASHP defrost bell (12% max at
  ~2C, shape per heatpumpmonitor.org (dagger), level re-anchored).
  eta per route calibrated by bisection so trailing-12m SPF == anchor.
- GATES (Phase B refuses to slice on failure): G1 SPFs within 0.01 of
  2.80/3.24/5.0; G2 network annual replacement electricity within 3%
  of the weekly model's what-if figure.
- Persistence: docs/retro.json, schema 1, append-only with 2-day
  revision window, ~200 KB/year, COPs derived not stored.
- VALIDATED offline (run_test_retro.py, shared-weather hourly stubs):
  anchors reproduced exactly; G2 passes at 1% / fails at 10%; cold-hour
  ordering at -4.6C: ashp 1.89 < shallow 2.49 < network 3.77 (the
  stress panel's story in one line); defrost notch present; append
  keeps frozen days byte-identical; eta stable across append; eta
  values 0.32-0.46 = plausible real-machine Carnot fractions, i.e. the
  calibration is not torturing physics to hit the anchors.
- First real-data run happens at Phase B integration; expect the
  Actions log to print the eta triple and both gate verdicts.

## Deployment order
1. scripts/build.py → run the Action once → check the log for gas_window and
   "history: N weeks".
2. docs/index.html (safe to deploy before or after — the layer waits for
   history).
3. README.md, docs/uk-heatsplit-methodology.pdf.

## Maintenance
- EXTEND CAP_HISTORY quarterly (next: Ofgem announces ~26 Aug 2026 for Oct).
- VERIFY the 2025-04-01 cap row (6.99/27.03 — from training data, not
  re-confirmed) before the backfill window reaches it… it already does: the
  backfill starts ~Jun 2025, priced from that row until 2025-07-01. Check
  against Ofgem's published Apr–Jun 2025 rates and correct if needed; entries
  are frozen after first computation, so fix the row BEFORE the first
  production run, or delete data.json history once and let it re-backfill.
- Consider committing data.json via the Action as now doubles as history backup
  (it already is committed daily — nothing to change, just don't force-push over
  it).

## Deliberate deviations from the spec sketch
- Calendar weeks (Mon–Sun), not rolling last-7-days: idempotency needs stable
  keys. The last ticker point can therefore differ from the rolling hero figure
  by a few days of weather.
- Non-cap fuel prices, ECUK vintage, INDIG shares held at today's values across
  the window (no weekly series exists) — stated in history_note and methodology
  §9.2.
- Entries frozen after the 2-week revision window: the series is an archive of
  what was published, not a rolling re-estimate.

## Testing done (offline, stub feeds in stubs/, harness run_test.py + test_page.js)
- build.py vs build_orig.py: byte-identical output except updated/price_tag/new
  keys; 57-week backfill, Sundays only, sorted, unique; rerun idempotent with
  exactly 2 CI refetches; error path preserves history; cap_prices resolves all
  test dates.
- index.html via jsdom: 20 assertions — hero fill, 4 sparklines with actual/
  what-if/gap, deltas (pp for indig), window switching changes deltas,
  no-history and 1-entry degradation, trendnote fill, v5.0.0.
