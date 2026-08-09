// test_capital.js - the capital panel.
//
// This is the only panel on the site with user inputs, so it needs a
// different kind of guard from the rest: the DEFAULT position is what the
// frontispiece and the ticker quote, and a refactor of the sliders must not
// move it silently. These assertions pin the published case, check the
// levers each push the answer the way they should, and confirm nothing goes
// non-finite at the extremes.
//
//   node test_capital.js      (needs docs/index.html, docs/data.json, jsdom)

const { JSDOM } = require("jsdom");
const fs = require("fs");
const html = fs.readFileSync("docs/index.html", "utf8");
const data = JSON.parse(fs.readFileSync("docs/data.json", "utf8"));

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
w.Plotly = { newPlot: () => {}, react: () => {} };
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(data) });
w.eval(html.match(/<script>([\s\S]*?)<\/script>/)[1]);

setTimeout(() => {
  const g = id => w.document.getElementById(id);
  const fail = [];
  const chk = (name, cond, detail) => {
    if (cond) { console.log("  ok   " + name); }
    else { fail.push(name); console.log("  FAIL " + name +
                                        (detail ? " - " + detail : "")); }
  };
  const at = over => {
    Object.assign(m.S, m.def, over || {});
    return m.compute();
  };

  console.log("capital panel");

  const m = w.capitalModel;
  chk("panel initialised against the live payload", !!m);
  if (!m) { console.log("\n1 FAILED"); process.exit(1); }
  chk("panel is visible", g("capitalpanel").style.display === "block");

  // --- the live input
  chk("saving is annual and plausible",
      m.savingBn > 1.5 && m.savingBn < 6,
      "GBP " + m.savingBn.toFixed(2) + "bn/yr");
  chk("saving is a 12m figure, not a week",
      m.savingBn > 20 * (data.headlines.whatif_20pct_geothermal
        ? (data.cost.national_week_total_Mgbp
           - data.headlines.whatif_20pct_geothermal.bill_Mgbp) / 1000 : 0),
      "must not be one week annualised by accident");
  chk("strip is about a fifth of delivered heat and cool",
      m.stripTWh > 50 && m.stripTWh < 110,
      m.stripTWh.toFixed(1) + " TWh/yr");

  // --- the published default. If this moves, the frontispiece and the
  // ticker are quoting a number the page no longer shows.
  const d0 = at();
  chk("default is the published case (70/70/6%/30y, GBP 250 + 200)",
      m.def.mix === 70 && m.def.capture === 70 && m.def.rate === 60 &&
      m.def.tenor === 30 && m.def.dist === 250 && m.def.conn === 200,
      JSON.stringify(m.def));
  chk("default coverage is below 100% - a stated gap, not self-financing",
      d0.cov < 100,
      Math.round(d0.cov) + "%");
  chk("default coverage is in the band the frontispiece quotes (30-50%)",
      d0.cov > 30 && d0.cov < 50, Math.round(d0.cov) + "%");
  chk("default gap is positive and material",
      d0.gapBn > 10, "GBP " + d0.gapBn.toFixed(1) + "bn");

  // --- both adders default ON. Defaulting either off would be the most
  // flattering choice available and the first thing a reviewer checks.
  chk("distribution adder defaults on", m.def.dist > 0, "dist=" + m.def.dist);
  chk("connections adder defaults on", m.def.conn > 0, "conn=" + m.def.conn);
  chk("removing distribution improves coverage", at({dist:0}).cov > d0.cov);
  chk("removing connections improves coverage", at({conn:0}).cov > d0.cov);
  chk("default adders sum to the sourced central (GBP 450/annual MWh)",
      m.def.dist + m.def.conn === 450,
      "GBP " + (m.def.dist + m.def.conn));

  // --- the adders are ABSOLUTE per annual MWh, not a share of plant cost.
  // The bug this replaced multiplied the class blend by (1+d), which
  // treats a share-of-TOTAL benchmark as a share of plant and understates
  // the build. A pound added to an adder must move the network share of
  // the blend by a pound, no more and no less.
  const b0 = at({mix:100, conn:0, dist:0}).blend;
  const b1 = at({mix:100, conn:0, dist:100}).blend;
  chk("distribution adder is absolute, not proportional",
      Math.abs((b1 - b0) - 100 * (1 + 0.20)) < 0.5,
      "GBP 100 of adder moved the blend by GBP " + (b1 - b0).toFixed(1));

  // --- adders fall on the network share only. An individual ground-source
  // install has no main and no connection; charging it for one would
  // double-count against its own class anchor.
  chk("adders do not touch an all-GSHP mix",
      Math.abs(at({mix:0, dist:600, conn:900}).blend -
               at({mix:0, dist:0,   conn:0}).blend) < 0.5,
      "all-GSHP blend moved when the network adders were raised");

  // --- development and permits are added, and stated
  chk("development uplift is applied", (() => {
        const c = at({mix:100, dist:0, conn:0});
        const net = (180 + 305) / 2;
        return Math.abs(c.blend - net * 1.20) < 1;
      })(), "expected the class central plus 20%");
  chk("coverage caption names the development uplift",
      /development and permits/i.test(g("cap_cov_cap").textContent));

  // --- the levers each move the answer the right way
  chk("more network share lowers unit capital",
      at({mix:100}).blend < at({mix:0}).blend);
  chk("bulk connections are far cheaper than individual dwelling ones",
      at({conn:40}).blend < at({conn:900}).blend - 500);
  chk("all-networks improves on the default", at({mix:100}).cov > d0.cov,
      Math.round(at({mix:100}).cov) + "%");
  chk("all-GSHP is the worst mix", at({mix:0}).cov < d0.cov,
      Math.round(at({mix:0}).cov) + "%");
  chk("higher capture finances more", at({capture:100}).financeBn > d0.financeBn);
  chk("dearer money finances less", at({rate:120}).financeBn < d0.financeBn);
  chk("longer tenor finances more", at({tenor:40}).financeBn > d0.financeBn);
  chk("build cost is independent of the finance levers",
      Math.abs(at({rate:120, tenor:15, capture:30}).buildBn - d0.buildBn) < 0.01);

  // --- nothing goes non-finite anywhere on the ranges
  let bad = null;
  for (let mix = 0; mix <= 100 && !bad; mix += 10)
    for (let cap = 30; cap <= 100 && !bad; cap += 10)
      for (const rate of [30, 60, 120])
        for (const tenor of [15, 25, 40])
          for (const dist of [0, 250, 600])
            for (const conn of [0, 200, 900]) {
              const c = at({mix, capture: cap, rate, tenor, dist, conn});
              if (![c.financeBn, c.buildBn, c.blend, c.cov, c.hurdle]
                    .every(Number.isFinite)) {
                bad = JSON.stringify({mix, cap, rate, tenor, dist, conn});
              }
            }
  chk("finite across the whole lever space", !bad, bad);

  // --- the reset control restores the published case exactly
  at({mix:0, capture:100, rate:120, tenor:15, dist:0});
  g("cap_mix").value = 0;
  g("cap_mix").dispatchEvent(new w.Event("input"));
  chk("moving a lever reveals the reset control", !g("cap_reset").hidden);
  chk("moved lever is marked", g("cap_mix_v").classList.contains("moved"));
  g("cap_reset").click();
  chk("reset restores every lever",
      JSON.stringify(m.S) === JSON.stringify(m.def), JSON.stringify(m.S));
  chk("reset hides the reset control", g("cap_reset").hidden);
  chk("reset clears the moved marking",
      !g("cap_mix_v").classList.contains("moved"));

  // --- the panel says what kind of object it is
  chk("panel declares it is a calculator, not a live estimate",
      /Calculator/i.test(g("capitalpanel").textContent));
  chk("note states the saving is pinned to 12 months",
      /twelve months/i.test(g("cap_note").textContent));
  chk("note states the spread risk",
      /spread/i.test(g("cap_note").textContent));
  chk("note states the denominator is heat and cooling",
      /heat AND cooling|heat and cooling/i.test(g("cap_note").textContent));
  chk("note states adders fall on the network share only",
      /network share only/i.test(g("cap_note").textContent));
  chk("evidence fold corrects the ambient-loop intuition",
      /not reliably cheaper per metre/i.test(g("cap_classnote").textContent));
  chk("evidence fold names the distribution sources",
      /DECC\/AECOM|DESNZ 2024/i.test(g("cap_classnote").textContent));
  chk("evidence fold lists every class unblended",
      ["ATES", "doublet", "GSHP"].every(k =>
        new RegExp(k, "i").test(g("cap_classes").textContent)));

  const d1 = at();
  console.log("\npublished case: financed GBP %sbn | build GBP %sbn (GBP %s/MWh-yr)" +
              " | coverage %s%% | gap GBP %sbn",
              d1.financeBn.toFixed(1), d1.buildBn.toFixed(0),
              Math.round(d1.blend), Math.round(d1.cov), d1.gapBn.toFixed(1));
  console.log(fail.length ? "\n" + fail.length + " FAILED" : "\nall passed");
  process.exit(fail.length ? 1 : 0);
}, 500);
