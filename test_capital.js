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
  chk("default benefit-cost ratio is 2.05", near(c.bcr, 2.05, 0.02), c.bcr.toFixed(3));
  chk("default net present social value is about +19bn", near(c.npsv, 19, 1),
      c.npsv.toFixed(1));
  chk("default social cost is about 18bn", near(c.cost, 18, 1), c.cost.toFixed(1));
  chk("five levers, no more", D.querySelectorAll("#capitalpanel input[type=range]").length === 5);

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

  // ---- Annex A: both sides, and both responsive -------------------------
  reset();
  const f = M.financing();
  chk("the premium is priced against the gilt, not the hurdle", f.premium > 0);
  chk("what it buys splits into construction and subsurface",
      f.construction > 0 && typeof f.geological === "number");
  chk("construction risk is the larger term", f.construction > f.geological);
  reset(); set("ob", 20);  const buysLow  = M.financing().transferred;
  reset(); set("ob", 66);  const buysHigh = M.financing().transferred;
  chk("optimism bias drives BOTH sides of the Annex A test", buysHigh > buysLow,
      buysLow.toFixed(1) + " -> " + buysHigh.toFixed(1));
  reset(); set("loss", 40);
  chk("a shortfall raises the geological risk transferred",
      M.financing().geological > f.geological);

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
    ["the TS1 sizing basis is cited", /TS1 requirement 1\.7\.15/],
    ["the Annex A test is named as two-sided", /risk transfer and delivery efficiencies outweigh/],
    ["the counterfactual's provenance is on the face", /Electrification of Heat, 742 monitored units/],
    ["the peak claim is flagged as model-derived", /model of instantaneous performance, not a field trial/],
    ["the reference-class gap is stated", /no UK dataset/]
  ]) chk(what, re.test(txt));

  console.log(fails ? "\n" + fails + " of " + run + " FAILED"
                    : "\nall passed (" + run + " checks)");
  process.exit(fails ? 1 : 0);
}, 900);
