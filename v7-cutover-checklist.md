# UK Heat Split v7 — cutover checklist

Merging `UK-Heat-Split-v7` into `main`. Do it on a **weekday morning**, after
the day's scheduled run has completed, so a failure has the whole day to be
noticed and fixed. Allow about an hour, most of it waiting.

Everything is reversible until step 5. Steps 1–3 change nothing on the live
site.

---

## 0. Preconditions — do not start without these

- [ ] **2FA is enabled on the GitHub account.** Deadline was 26 August 2026;
      without it, account actions can be restricted mid-merge, which is the
      worst possible moment to find out.
- [ ] **Today's scheduled run on `main` is green.** Actions tab → filter
      branch `main` → the most recent "Update heat split" says *Scheduled*
      and has a green tick. If it failed, fix that first — never cut over
      onto a broken baseline.
- [ ] **The branch's last dispatch is green** and its log shows the four
      retro lines (`retro gates: PASS`, `retro eta:`, `retro stress:`,
      `retro binding hour:`).
- [ ] You have **an hour and no other deadline**. If the merge misbehaves,
      the fix is unhurried rollback, not improvisation.

---

## 1. Refresh the branch's documents (5 min)

The site links to the methodology PDF from `docs/`, so the new one must be in
the branch before the merge.

- [ ] Upload **`uk-heatsplit-methodology.pdf`** (v7.1.0, August 2026) to
      `docs/` on the branch, replacing the v5 file.
- [ ] Upload **`README.md`** (root) — the Phase E version with the hourly
      engine section and the autumn maintenance table.
- [ ] Upload **`RELEASE-v7.md`** (root).
- [ ] Confirm the branch also holds the final code set: `scripts/build.py`,
      `scripts/retro.py`, `docs/index.html`, `stubs/fetch_hourly.py`,
      `run_test.py`, `run_test_retro.py`, `test_page.js`, `NOTES.md`.

## 2. Protect the data artefacts (5 min)

`docs/data.json` and `docs/retro.json` exist on both branches with different
content. Without this step git will try to merge them line by line, which for
minified JSON means a corrupted file.

- [ ] Create **`.gitattributes`** in the repository root **on the branch**
      with exactly:

      docs/data.json merge=ours
      docs/retro.json merge=ours

- [ ] Commit it to the branch.

*What this does: on any merge conflict in those two files, keep the version
already on the branch being merged into. They are rebuilt by the next run
anyway, so their content at merge time does not matter — only that the file
stays valid JSON.*

## 3. Tag the escape route (2 min)

- [ ] On GitHub: **Releases → Draft a new release → Choose a tag → create new
      tag** `v6.7.0-pre-v7` targeting **main**. Title it "v6.7.0 — last state
      before v7 cutover". Publish.

*This is the rollback point. It costs nothing and makes step 8 trivial.*

---

## 4. Check the workflow one last time (5 min)

Open `.github/workflows/update.yml` **on the branch** and confirm all three:

- [ ] Under `on:` there is **both** `schedule:` (with the cron) **and**
      `workflow_dispatch:` — nested under the single `on:` key. Two separate
      `on:` keys silently discard the first, and the cron would vanish at
      cutover without any error.
- [ ] The pull line reads `git pull --rebase origin ${GITHUB_REF_NAME}` —
      not `origin main`.
- [ ] The add step is conditional:
      `[ -f docs/retro.json ] && git add docs/retro.json || true`

## 5. Merge (5 min) — the live site changes here

- [ ] **Pull requests → New pull request**: base `main` ← compare
      `UK-Heat-Split-v7`.
- [ ] Read the file list. Expect: `scripts/build.py`, `scripts/retro.py`,
      `docs/index.html`, `docs/uk-heatsplit-methodology.pdf`, `README.md`,
      `NOTES.md`, `RELEASE-v7.md`, `.gitattributes`, the three test files,
      `stubs/`, and the two data files. Anything unexpected — stop and ask.
- [ ] Create the pull request, then **Merge pull request** (a normal merge
      commit; do not squash — the branch history is the audit trail).

## 6. Dispatch immediately (5 min)

Do not wait for tomorrow's cron: the merged code should prove itself now,
while you are watching.

- [ ] Actions → Update heat split → **Run workflow** → branch **main** → Run.
- [ ] Watch it to completion (~2–3 minutes).

## 7. Verify (10 min)

**In the run log:**

- [ ] `retro system tail: demand-data-update supplied N of N lagging days`
- [ ] `retro gates: PASS` with all four gates true
- [ ] `retro eta:` approximately `{ashp 0.33, shallow 0.34, network 0.49}`
- [ ] `retro stress: worst hour ... 136.7 GWh` and additions near
      `{ashp 14.3, shallow 10.3, network 6.8}`
- [ ] `retro binding hour:` with a requirement near 49 GW and headroom near
      +13 GW
- [ ] `wrote .../docs/data.json` and no traceback

**On the live site** (`causewaygt.github.io/uk-heatsplit`, hard-refresh —
Pages takes a minute or two after the commit):

- [ ] The **Electricity grid impacts** panel appears, between the energy bars
      and the geothermal panel.
- [ ] All four card rows populate: coldest hour, tightest hour, the binding
      bars, hottest hour.
- [ ] The **Monthly · falcon** toggle draws Jan→Dec (not a flat line — that
      was the date-parsing fault).
- [ ] The tightest-week chart shows the capacity line above actual use, with
      the binding hour annotated inside the window.
- [ ] The panel footer is **one short paragraph**, not a wall of text.
- [ ] The methodology link opens the **v7.1.0** PDF.
- [ ] The hero, bars, cooling, costs, carbon, NI and WHY HEAT panels are all
      unchanged and populated.

## 8. If anything is wrong — rollback (5 min)

- [ ] Actions → find the merge commit → or simply: **Releases → v6.7.0-pre-v7
      → ... ** and restore `scripts/build.py`, `docs/index.html` from that
      tag, deleting `scripts/retro.py`.
- [ ] Simpler alternative: on the merge commit page, use **Revert** to open a
      revert PR, and merge it.
- [ ] Then dispatch once on main and confirm the v6.7 page returns.

*The v7 branch is untouched by a revert, so nothing is lost; the fault can be
diagnosed at leisure and the cutover repeated.*

---

## 9. The morning after — the one check that matters

- [ ] Confirm the **scheduled** run fired overnight (Actions, branch `main`,
      event *Scheduled*, green) and that the site's timestamp moved.

This is the only silent failure mode in the whole system: a tracker that
stops updating without anyone noticing. If the cron did not fire, the `on:`
block is the first place to look.

## 10. Housekeeping (same week)

- [ ] Delete or archive the `UK-Heat-Split-v7` branch **only after** a week
      of green scheduled runs.
- [ ] Send the peer-review pack: v7 page URL, methodology PDF v7.1.0, README,
      audit memo. Cover note angle: the tracker caught and declared its own
      corrections during the build — the coincidence premium was wrong by a
      third until the trailing-year fix, and the summer heat spikes exposed a
      shaping inconsistency with the weekly estimator.

## Diary — set reminders now

| When | What |
|---|---|
| **October 2026** | NESO Winter Outlook 2026/27 → update `DISPATCH_DERATED_GW` in `scripts/retro.py` and its dagger. The headroom figures are stale until this is done. |
| **September 2026** | Cooling-reconciliation gate decision (a full observed season in hand). |
| **Autumn 2026** | ECUK 2026 re-anchor: heat-pump electricity, domestic shares, sectoral revisions. |
| **Quarterly** | Ofgem cap history row. |
