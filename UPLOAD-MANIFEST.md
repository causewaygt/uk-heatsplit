# v7 branch upload — manifest

Upload to **UK-Heat-Split-v7** (check the branch selector before every commit).
Unzip locally first; the folder structure below is the repository structure.

| File | Destination | Note |
|---|---|---|
| `scripts/build.py` | `scripts/` | retro-wired, 13-month window, netting input, binding-hour log line |
| `scripts/retro.py` | `scripts/` | hourly engine: schema 2, summer damping, DHW flow split, system view, short basis |
| `docs/index.html` | `docs/` | v7.0.0 panel, plain copy, split legends, adaptive gas key |
| `docs/uk-heatsplit-methodology.pdf` | `docs/` | **v7.1.0, August 2026** — replaces the v5 file the site links to |
| `stubs/fetch_hourly.py` | `stubs/` | stub twins incl. system feeds with injected truths |
| `stubs/stub_weather.py` | `stubs/` | unchanged, included for completeness |
| `run_test.py` | root | weekly suite |
| `run_test_retro.py` | root | engine suite (A′/B/B.3/B.4 sections) |
| `test_page.js` | root | page suite |
| `README.md` | root | Phase E: hourly engine section, autumn maintenance table |
| `NOTES.md` | root | running build notes / design of record |
| `RELEASE-v7.md` | root | release notes |
| `.gitattributes` | root | **merge=ours on the two data files — required before cutover** |
| `v7-cutover-checklist.md` | root | the merge procedure |

**Not included, deliberately:** `docs/data.json` and `docs/retro.json` — these
are build artefacts, written by the workflow, never uploaded by hand.

**After uploading:** dispatch the workflow on the branch once and confirm the
log shows `retro gates: PASS`, η ≈ 0.33 / 0.34 / 0.49, worst hour 136.7 GWh
with additions ≈ 14.3 / 10.3 / 6.8 GW, and a binding-hour requirement ≈ 49 GW.
Then follow `v7-cutover-checklist.md`.
