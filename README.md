# UK Heat Split — a weekly estimate of how Britain heats itself

Most of Britain's heat still comes from burning gas, and a material share of
every unit burned never becomes useful heat. This site turns live operational
data into a weekly estimate of the GB heating energy split — and, in later
phases, the role geothermal heat (deep, mine water, and ground source heat
pumps) plays and could play in replacing combustion.

**Live site:** https://toddsp.github.io/uk-heatsplit/

## Method in one paragraph
Daily National Transmission System gas demand is regressed against
population-weighted GB heating degree days (ERA5 reanalysis via Open-Meteo).
The temperature-sensitive component is attributed to space heating; the flat
baseline is hot water, cooking, industrial and power-station gas. The split is
calibrated against the annual DESNZ ECUK/DUKES structure. The approach follows
the published Watson/Sansom method. **Every live figure is a model estimate,
not a measurement** — uncertainties and caveats are stated on the site.

## Roadmap
- **Phase 1 (live):** gas-driven heating estimate + boiler-waste comparison
- **Phase 2:** electric heating layer (NESO/Elexon)
- **Phase 3:** geothermal panel — estimated actuals plus source-tagged
  12-month and 5-year forecasts (CCC, DESNZ, Project InnerSpace)
- **Phase 4:** cooling sliver (weakest data, widest error bars)

## Data sources & licences
National Gas Transmission open data · Elexon BMRS (© Elexon Ltd) · NESO Open
Licence · Open-Meteo.com (CC BY 4.0) / Copernicus ERA5 · DESNZ statistics under
the Open Government Licence v3.0.

## Running it
A GitHub Actions cron (`.github/workflows/update.yml`) runs daily, rebuilding
`docs/data.json` via `scripts/build.py`. Fork-friendly; no API keys required.
Before treating outputs as publishable: replace the ECUK anchor placeholder in
`scripts/build.py` and confirm the calibration ratio is within ±10%.

*A Causeway Energies public-interest tool.*
