// test_capital.js - the value for money panel, rebuilt 10 Aug 2026.
//
// The panel is the only thing on this site driven by user inputs, and the
// frontispiece quotes it. So this suite pins three things: that the published
// DEFAULT does not move without someone meaning it to, that the panel and
// point 06 cannot disagree, and that the model stays coherent across the whole
// lever space rather than only at the settings we happened to look at.
const { JSDOM } = require("jsdom"); const fs = require("fs");
const html  = fs.readFileSync("docs/index.html", "utf8");
const data  = JSON.parse(fs.readFileSync("docs/data.json", "utf8"));
const retro = JSON.parse(fs.readFileSync("docs/retro.json", "utf8"));

let fails = 0, run = 0;
const chk = (name, ok, note) => {
  run++; if (!ok) fails++;
  console.log((ok ? "  ok   " : "  FAIL ") + name + (note ? "   " + note : ""));
};
const near = (a, b, tol) => Math.abs(a - b) <= tol;

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
w.Plotly = { newPlot: () => {}, react: () => {} };
w.fetch = u => Promise.resolve({ json: () => Promise.resolve(
  String(u).includes("retro") ? retro : data) });
w.eval(html.match(/<script>([\s\S]*?)<\/script>/)[1]);

setTimeout(() => {
  const D  = w.document;
  const M  = w.capitalModel;
  const el = id => D.getElementById(id);
  const set = (k, v) => { const e = el("lv_" + k); e.value = v;
                          e.dispatchEvent(new w.Event("input")); };
  const DEF = { cap: 14, ob: 50, pk: 75, sizing: 50, loss: 10 };
  const reset = () => Object.entries(DEF).forEach(([k, v]) => set(k, v));

  // The panel can be HELD - one flag covers it and the frontispiece point that
  // reads it. When held there is nothing to check but the cover itself, and
  // the important thing is that no figure or lever is left exposed behind it.
  const held = !M;
  const panelHeld = !!el("capitalpanel").querySelector(".undercon");
  chk("panel and its model are held together", held === panelHeld,
      "model " + (held ? "off" : "on") + ", panel " +
      (panelHeld ? "covered" : "live"));

  if (held) {
    chk("the cover is labelled work in progress",
        /work in progress/i.test(el("capitalpanel").textContent));
    // Deliberately says nothing about WHAT is being worked on - a cover
    // that describes the open questions tells a reader more than a held
    // panel should.
    chk("the cover gives nothing away about the work",
        el("capitalpanel").textContent.replace(/\s+/g, " ").trim()
          === "Value for moneyWork in progress".replace(/\s+/g, " "));
    chk("no live levers are left behind the cover",
        el("capitalpanel").querySelectorAll("input[type=range]").length === 0);
    chk("no orphan figure is left behind the cover",
        !/benefit-cost ratio|\u00d71\.|per cent of capital/i
          .test(el("capitalpanel").textContent));
    const p6h = [...D.querySelectorAll(".pt")].pop();
    chk("the frontispiece point is covered too",
        p6h.classList.contains("undercon"));
    chk("the held point carries no figure and no prose",
        !p6h.querySelector(".fig") && !p6h.querySelector("p"));
    console.log("\n  panel held - cover checks passed, model checks skipped");
    console.log(fails ? fails + " of " + run + " FAILED"
                      : "all passed (" + run + " checks)");
    process.exit(fails ? 1 : 0);
  }

  chk("the panel is visible - no cover left on it",
      el("capitalpanel").style.display === "block");

  // ---- the published default -------------------------------------------
  // Pinned so a refactor cannot quietly move what the frontispiece quotes.
  reset();
  const c = M.compute();
  // Defaults moved 12 Aug 2026 when the cooling lever landed at 30% of
  // connections. At zero cooling the case is the old one, 2.05 - and the
  // check below pins that too, so the cooling contribution stays visible
  // rather than being absorbed into a new baseline.
  chk("default benefit-cost ratio is 2.53", near(c.bcr, 2.53, 0.02), c.bcr.toFixed(3));
  chk("default net present social value is about +30bn", near(c.npsv, 30, 1),
      c.npsv.toFixed(1));
  chk("default social cost is about 19bn", near(c.cost, 19, 1), c.cost.toFixed(1));
  chk("six levers, no more", D.querySelectorAll("#capitalpanel input[type=range]").length === 6);
  // The cooling lever must be able to go to zero and leave the heat case
  // standing on its own. A lever that improves both sides of the ratio at
  // once has to be removable, or a reader cannot tell what it is worth.
  (() => {
    const lv = D.getElementById("lv_cool");
    if(!lv){ chk("cooling lever exists", false); return; }
    lv.value = 0; lv.dispatchEvent(new w.Event("input"));
    const zero = w.capitalModel.compute();
    chk("at zero cooling the heat case stands alone at 2.05",
        near(zero.bcr, 2.05, 0.02), zero.bcr.toFixed(3));
    lv.value = 30; lv.dispatchEvent(new w.Event("input"));
  })();

  // ---- panel and frontispiece cannot disagree ---------------------------
  const p6 = [...D.querySelectorAll(".pt")].find(p => {
    const n = p.querySelector(".n"); return n && n.textContent === "06"; });
  chk("frontispiece point 06 exists", !!p6);
  if (p6) {
    const fig = p6.querySelector(".fig").textContent.replace(/[^0-9.].*$/, "");
    chk("point 06 quotes the panel's own ratio", fig === c.bcr.toFixed(2),
        fig + " vs " + c.bcr.toFixed(2));
    chk("point 06 claim matches whether it clears",
        c.bcr >= 1 ? /pays for itself\./.test(p6.querySelector(".claim").textContent)
                   : /does not yet pay/.test(p6.querySelector(".claim").textContent));
    chk("point 06 names the counterfactual as a network, not a boiler",
        /air-source network/.test(p6.textContent));
  }

  // ---- the levers actually drive it -------------------------------------
  const bcrNow = () => M.compute().bcr;
  reset(); set("ob", 20);  const lowOb  = bcrNow();
  reset(); set("ob", 66);  const highOb = bcrNow();
  chk("optimism bias moves the ratio the right way", lowOb > highOb,
      lowOb.toFixed(2) + " > " + highOb.toFixed(2));

  reset(); set("loss", 0);  const noLoss = bcrNow();
  reset(); set("loss", 40); const bigLoss = bcrNow();
  chk("a subsurface shortfall reduces the benefit", noLoss > bigLoss,
      noLoss.toFixed(2) + " > " + bigLoss.toFixed(2));

  // The increment is bought by the kW, so rating the plant higher must cost
  // more. This was a real bug: sizing bought benefit without buying cost.
  reset(); set("sizing", 40);  const small = M.compute();
  reset(); set("sizing", 100); const large = M.compute();
  chk("rating the plant higher costs more capital", large.cost > small.cost,
      small.cost.toFixed(1) + " -> " + large.cost.toFixed(1));
  chk("sizing is not a free lunch - the ratio falls as it rises",
      small.bcr > large.bcr, small.bcr.toFixed(2) + " > " + large.bcr.toFixed(2));

  // ---- coherence across the whole lever space ---------------------------
  reset();
  let bad = null;
  const RANGE = { cap: [6, 26], ob: [0, 66], pk: [0, 120],
                  sizing: [40, 100], loss: [0, 40] };
  for (const k in RANGE) for (const v of RANGE[k]) {
    reset(); set(k, v);
    const x = M.compute();
    if (!isFinite(x.bcr) || x.bcr < 0 || !isFinite(x.cost) || x.cost <= 0)
      bad = k + "=" + v;
    if (/NaN|Infinity|undefined/.test(el("capitalpanel").textContent))
      bad = k + "=" + v + " (rendered)";
  }
  chk("finite and clean at every lever extreme", !bad, bad || "");
  reset();

  // ---- the things the panel must keep saying ----------------------------
  const txt = el("capitalpanel").textContent;
  for (const [what, re] of [
    ["it is published against interest", /published against interest/],
    ["pre-FID development capital is excluded", /Pre-FID development capital is excluded/],
    ["the European risk funds are named, and the UK gap", /the UK has none/],
    ["the TS1 sizing basis is cited", /TS1 requirement 1\.7\.15/],
    // The financing sub-panel came out on 10 Aug 2026: Annex A compares
    // EXCHEQUER costs, and a heat network is a commercial asset funded by heat
    // sales, so the test does not bite at programme level. What replaced it is
    // one paragraph saying so - and saying where the test does apply.
    ["who pays is answered, not performed", /funded by heat sales/],
    ["Annex A is placed at scheme level, not here", /applies at scheme level/],
    ["the counterfactual's provenance is on the face", /Electrification of Heat, 742 monitored units/],
    ["the peak claim is flagged as model-derived", /model of instantaneous performance, not a field trial/],
    ["the reference-class gap is stated", /no UK dataset/]
  ]) chk(what, re.test(txt));

  console.log(fails ? "\n" + fails + " of " + run + " FAILED"
                    : "\nall passed (" + run + " checks)");
  process.exit(fails ? 1 : 0);
}, 900);
