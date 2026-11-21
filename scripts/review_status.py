"""Peer-review item status, checked against the code rather than remembered.

WHY THIS EXISTS. The 20 review items were tracked in conversation. That record
does not survive, and on 20 Nov 2026 it failed three ways in one afternoon: a
carried-forward note said items 9-18 were unstarted and item 8 was next, which
was wrong; re-deriving status by hand called item 1 unstarted because the probe
searched for "reconcile" while the block is named "denominators"; and the
review document itself was hunted through memory and old chats while sitting on
disk. One cause - the status lived nowhere a check could reach.

So it lives here, next to the code it describes, and every item that CAN be
checked mechanically is. This is the same rule as check_undemeaned_pin(): a
status the repository states about code it does not read is a status that will
drift.

    python3 scripts/review_status.py           # the table
    python3 scripts/review_status.py -v        # with the marker that was tested

TWO LAYERS, and the distinction is the lesson of 20 Nov. CODE is whether the
work is in the repository. PAYLOAD is whether docs/data.json actually carries
what the front end needs. They are not the same: items 1 and 2 were complete in
the code for a day while the live page showed an em dash and fell back to
literals, because the store and payload predated the code and every consumer
guards with a silent `if(!D) return;`. An item is not live until both pass.

Statuses are AGREED ACTIONS from the response document of 19 Nov 2026. Where an
item cannot be decided by inspection - an external run, a judgement about copy -
it is marked MANUAL and says what to look at. Never guess one of those.

CHOOSING A MARKER. Do not search for a published FIGURE. This site states
almost nothing as a literal - figures are read from the payload on purpose, so
that prose cannot drift from the model - and on 20 Nov 2026 items 1 and 4 were
both called unstarted after grepping for "reconcile" and for "1.96 / 2.86". The
block is named "denominators" and the band is computed by bcrBand(); neither
figure appears anywhere. In this codebase the ABSENCE of a literal is evidence
the work was done properly. Mark an item by the name of the thing that computes
it, and when a check fails, read the file before believing it.

AND LOOK IN THE RIGHT FILE. Three markers failed in one hour on 20 Nov 2026,
all the same way: grepping docs/index.html for prose. Evidence on this site
lives in payload files the page FETCHES - spf_evidence.json, geo_sites.json,
daily.json - and computed figures live in build.py and retro.py. The page is
mostly a reader. Before marking an item open, check the payload files and the
build scripts, and check origin as well as the working copy: a local
docs/*.json can be weeks behind what is live.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = "-v" in sys.argv or "--verbose" in sys.argv


def _read(rel):
    p = os.path.join(ROOT, rel)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def code(rel, pattern, want=True):
    """True when `pattern` is present in `rel` (or absent, if want=False)."""
    s = _read(rel)
    if s is None:
        return None, "%s unreadable" % rel
    hit = re.search(pattern, s) is not None
    return (hit == want), "%s %s /%s/" % (rel, "has" if hit else "lacks",
                                          pattern[:48])


def payload(path_keys):
    """True when docs/data.json carries the given nested key path."""
    s = _read("docs/data.json")
    if s is None:
        return None, "docs/data.json unreadable"
    try:
        node = json.loads(s)
    except ValueError:
        return None, "docs/data.json unparseable"
    for k in path_keys:
        if not isinstance(node, dict) or k not in node or node[k] in (None, [], {}):
            return False, "data.json missing %s" % ".".join(path_keys)
        node = node[k]
    return True, "data.json carries %s" % ".".join(path_keys)


def history_field(name):
    """True when EVERY history entry carries `name`.

    history is a list, so payload() cannot reach into it - and a check on
    the list itself would pass vacuously while the field was missing from
    every week in it. Every entry, not any: the front end's own guard is
    rows.every(), so a partial restatement degrades the panel and the
    status should say so rather than read done.
    """
    s = _read("docs/data.json")
    if s is None:
        return None, "docs/data.json unreadable"
    try:
        h = json.loads(s).get("history") or []
    except ValueError:
        return None, "docs/data.json unparseable"
    if not h:
        return False, "data.json has no history"
    n = sum(1 for e in h if isinstance(e, dict) and e.get(name))
    return (n == len(h)), "%d of %d history weeks carry %s" % (n, len(h), name)


def both(code_args, payload_keys):
    """Code AND payload - an item the front end reads from the build."""
    ok_c, why_c = code(*code_args)
    ok_p, why_p = payload(payload_keys)
    if ok_c is None or ok_p is None:
        return None, "; ".join(x for x in (why_c, why_p) if x)
    if ok_c and not ok_p:
        return "stale", why_c + "; " + why_p + " (rebuild needed)"
    return (ok_c and ok_p), why_c + "; " + why_p


MANUAL = "manual"

# (number, tier, one-line ask, checker or None)
ITEMS = [
    (1, 0, "One definition of '20% of UK heat'; reconcile the denominators",
     lambda: both(("scripts/build.py", r'hardware"\]\["denominators"\]'),
                  ("geothermal", "hardware", "denominators"))),
    (2, 0, "Parameterise SPF*/flow so both performance figures are derived",
     lambda: both(("scripts/retro.py", r"network_band = source_band"),
                  ("whatif_routes", "calibration", "network_band"))),
    (3, 0, "Cooling panel on revised figures; withdraw the sampling story",
     lambda: code("scripts/build.py", r'"annual_TWh": 3\.52')),
    # bcrBand() re-appraises across RBAND, so it is payload-dependent too:
    # `if(!RBAND.length) return null` and the band silently does not render.
    (4, 1, "Publish the headline saving as a band from loop temperature",
     lambda: both(("docs/index.html", r"function bcrBand\("),
                  ("whatif_routes", "calibration", "network_band"))),
    (5, 1, "Two-axis sensitivity: source temp against delivery temp",
     lambda: both(("scripts/retro.py", r"sensitivity = sensitivity_grid"),
                  ("whatif_routes", "calibration", "sensitivity"))),
    # The evidence is a payload file the page fetches, not prose in the page.
    # Marked on index.html first and reported OPEN while the research had been
    # done and shipped - the third marker to fail the same way in one hour.
    (6, 1, "Measured international SPF table on SEPEMO boundaries",
     lambda: code("docs/spf_evidence.json", r'"sepemo"')),
    (7, 1, "Gas estimate published as a band, not a pass/fail gate",
     lambda: code("scripts/build.py", r"gate_superseded_by_band")),
    (8, 2, "Multi-counterfactual: waste heat, water source, hybrid",
     lambda: code("docs/index.html", r"One counterfactual", want=False)),
    (9, 2, "Gas-grid coverage column; Sweden as ceiling not comparator",
     lambda: code("docs/index.html", r"gas-grid coverage|gas_grid_pct")),
    (10, 2, "Richard's 40-winter run, published beside ours", None),
    (11, 2, "Extend the record beyond two winters",
     lambda: code("scripts/build.py", r"RETRO_SPAN_DAYS = 1826")),
    (12, 3, "Frontispiece rebuilt as three beats, hero 2.1x", None),
    # Marked on the call SIGNATURE, not on a comment: the scale being handed
    # in is the fix, and a renamed comment would not change that.
    (13, 3, "Common scale on both states of the what-if bar",
     lambda: code("docs/index.html",
                  r"drawWhatIfBars\(agg, sc")),
    # Checkable since 21 Nov 2026: the ghost blocks need the payload field
    # as well as the drawing code, so both layers are tested.
    (14, 3, "Split geothermal out in its own green, direct labels",
     lambda: (lambda c, p: (None, c[1] + "; " + p[1]) if c[0] is None
              or p[0] is None else
              ("stale", c[1] + "; " + p[1]) if c[0] and not p[0] else
              (c[0] and p[0], c[1] + "; " + p[1]))(
                  code("docs/index.html", r"function drawGhost\("),
                  history_field("wf_fuels"))),
    # The buttons render upper case, so the marker is case-insensitive on the
    # rendered label rather than on the document's sentence-case wording.
    (15, 3, "Button labels 'Today' / 'With a geothermal fifth'",
     lambda: code("docs/index.html",
                  r"(?i)>\s*with a geothermal fifth\s*<")),
    (16, 3, "2030 with-and-without dispatchable capacity chart", None),
    (17, 3, "Audience one-pagers off the PNG export (later)", None),
    (18, 4, "NI register of installed systems at or above 45 kW",
     lambda: code("docs/geo_sites.json", r"\S")),
    (19, 4, "Corrections log stays in the methodology - not doing", None),
    (20, 4, "Three defects found in review",
     lambda: code("docs/index.html",
                  r"(peak test, not an energy test[\s\S]*){2,}", want=False)),
]

NOTE = {
    10: "external - Richard's run; check whether his result is on the page",
    12: "judgement on copy - read the frontispiece",
    14: "visual - look at the chart",
    16: "new chart - look for a 2030 panel",
    17: "deferred by agreement, lowest priority",
    19: "declined by agreement - nothing to build",
    20: "only the duplicated point-03 clause is checked here; the smoke-test "
        "scroll and the archive sources block are separate",
}

# DEFERRED BY DECISION, not by priority. An item here is not open and must not
# be offered as "next" - someone decided to leave it, and that decision belongs
# beside the code rather than in a chat log nobody can search. Record who and
# when, so it can be revisited on purpose instead of drifting back in.
# The underlying check still runs: if an item is later built anyway, it reports
# done rather than going on claiming to be deferred.
DEFERRED = {
    8: "21 Nov 2026, Simon: panel 6 stays on a single counterfactual for now",
}

LABEL = {True: "done", False: "OPEN", "stale": "CODE OK, PAYLOAD STALE",
         None: "CANNOT CHECK", MANUAL: "manual", "deferred": "deferred"}


def main():
    rows, counts = [], {}
    for num, tier, ask, chk in ITEMS:
        if chk is None:
            state, why = MANUAL, NOTE.get(num, "")
        else:
            state, why = chk()
            # A deferral hides OPEN but never hides done: if the work turns up,
            # say so rather than reporting a decision that has been overtaken.
            if num in DEFERRED and state is not True:
                state = "deferred"
                why = DEFERRED[num] + (" | " + why if why else "")
            if num in NOTE:
                why = (why + " | " + NOTE[num]) if why else NOTE[num]
        counts[state] = counts.get(state, 0) + 1
        rows.append((num, tier, ask, state, why))

    print("UK Heat Split - peer-review items, checked against the code")
    print("response document 19 Nov 2026; 20 items, 4 tiers\n")
    last_tier = None
    for num, tier, ask, state, why in rows:
        if tier != last_tier:
            print("--- Tier %d %s" % (tier, "-" * 56))
            last_tier = tier
        print("  %2d  %-22s %s" % (num, LABEL[state], ask))
        if V and why:
            print("      %s" % why)

    print("\n%s" % ("-" * 66))
    for k in (True, "stale", False, "deferred", None, MANUAL):
        if counts.get(k):
            print("  %-22s %d" % (LABEL[k], counts[k]))
    nxt = [r for r in rows if r[3] is False]
    if nxt:
        n = min(nxt, key=lambda r: (r[1], r[0]))
        print("\nNEXT (lowest open item in the lowest tier): %d - %s"
              % (n[0], n[2]))
    if counts.get("stale"):
        print("\nSTALE means the work is in the repository but docs/data.json "
              "does not carry it yet, so the page is still falling back. "
              "Run the build; do not call these items open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
