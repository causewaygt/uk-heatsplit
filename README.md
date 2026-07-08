# UK Heat Split — weekly estimate

Static dashboard estimating GB heating energy split from live gas demand and
ERA5 degree days, calibrated to annual ECUK/DUKES structure. Phase 1: gas only.

## Setup (once)
1. Create a **public** GitHub repo and push this tree.
2. Settings → Pages → Deploy from branch → `main` / `/docs`.
3. Actions tab → run **Update heat split** manually (`workflow_dispatch`) once.
   - If the National Gas catalogue name-matching fails, the log prints every
     available publication name — adjust `TARGETS` in `scripts/fetch_gas.py`.
4. Confirm `docs/data.json` was committed and the page renders.

Daily cron then runs at 06:17 UTC. The daily commit keeps the repo active,
which prevents GitHub's 60-day auto-disable of scheduled workflows.

## Before publishing (Phase 1 go/no-go)
- Replace `ECUK_ANNUAL_GAS_HEAT_TWH` placeholder in `scripts/build.py` with the
  sourced ECUK Table U3 value; require calibration ratio within ±10%.
- Confirm the National Gas attribution string (portal disclaimer page /
  box.operationalliaison@nationalgas.com) and update the footer.
- Sanity-check `MCM_TO_GWH` against the DESNZ annual calorific value.

## Fallback behaviour
Feed failure → previous values retained, source marked `stale`, status light
shown on page. Both feeds missing on first run → build exits with error.

## Roadmap
Phase 2 electricity heating (NESO/Elexon) · Phase 3 geothermal panel with
tagged forecasts · Phase 4 cooling sliver.
