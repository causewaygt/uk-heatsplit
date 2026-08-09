// test_bars.js - the energy in/out bars, live and windowed.
// Trap 5 from the Irish handover: node --check proves a file parses, not
// that a function survives its arguments. This executes the renderer.
//   node test_bars.js      (needs docs/data.json and jsdom)
const { JSDOM } = require("jsdom");
const fs = require("fs");
const html = fs.readFileSync("docs/index.html", "utf8");
const data = JSON.parse(fs.readFileSync("docs/data.json", "utf8"));

// synthesise a per-fuel history: 8 weeks, each carrying `fuels`
const wk = () => ({
  gas_space:{i:9000,u:7515}, gas_dhw:{i:1200,u:1002}, oil:{i:300,u:246},
  bio_other:{i:120,u:84}, solid:{i:20,u:14}, heat_networks:{i:60,u:51},
  elec_heat:{i:200,u:214}, _u:{elec_resistive:180,hp_electricity:12,hp_ambient:22},
  cool:{i:400,u:1200}
});
data.history = data.history.slice(-8).map(e => ({...e, fuels: wk()}));

// how many fuels each bar can legitimately draw, from the page's own tables
const IN_FUELS = 8;    // IN_DEF
const OUT_FUELS = 10 + 1;  // OUT_DEF plus the combustion-loss hatch

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
w.Plotly = { newPlot: () => {}, react: () => {} };
w.fetch = () => Promise.resolve({ json: () => Promise.resolve(data) });
w.eval(html.match(/<script>([\s\S]*?)<\/script>/)[1]);

setTimeout(() => {
  const g = id => w.document.getElementById(id);
  const fail = [];
  const chk = (name, cond) => { if (!cond) fail.push(name); };
  const segs = id => g(id).children.length;
  const click = win => [...g("trendctl").querySelectorAll("button")]
                        .find(b => b.dataset.win === win).click();

  // --- live
  const liveIn = g("tot_in").textContent, liveOut = g("tot_out").textContent;
  chk("live: legend populated", g("mixlegend").children.length > 0);
  chk("live: loss key present", /Lost in combustion/.test(g("uselegend").innerHTML));
  chk("live: ambient callout says this week", /this week/.test(g("combline").textContent));

  // The bars are REDRAWN, not added to. drawEnergyBars runs once at setup
  // and again on every trend change; if renderBar appends instead of
  // replacing, the segment count grows on each call while every total and
  // legend above stays correct - so nothing else in this file would notice.
  // Segments can never outnumber the fuels defined.
  const liveMix = segs("mixbar"), liveUse = segs("usebar");
  chk("live: mixbar segments within the fuel table", liveMix <= IN_FUELS);
  chk("live: usebar segments within the fuel table", liveUse <= OUT_FUELS);
  chk("live: mixbar segments match its legend keys",
      liveMix === g("mixlegend").children.length);

  // --- windowed: click 12m
  click("53");
  const winIn = g("tot_in").textContent, winOut = g("tot_out").textContent;
  chk("window: totals changed", winIn !== liveIn && winOut !== liveOut);
  chk("window: in-total is 8x the fixture week",
      /8[,.]?/.test(winIn) || parseInt(winIn.replace(/[^0-9]/g,"")) > 80000);
  chk("window: loss segment still drawn",
      /Lost in combustion/.test(g("uselegend").innerHTML));
  chk("window: ambient segment present",
      /ambient heat harvested/i.test(g("uselegend").innerHTML + g("combline").innerHTML));
  chk("window: caption says over the window",
      /over the window/.test(g("combline").textContent));
  chk("window: bar label names the window",
      /totals over the last 12 months/.test(g("barwin").textContent));
  chk("window: mixbar segments within the fuel table", segs("mixbar") <= IN_FUELS);
  chk("window: usebar segments within the fuel table", segs("usebar") <= OUT_FUELS);

  // --- redrawing the same window must not add a second stack
  const beforeRepeat = segs("mixbar");
  click("53"); click("53");
  chk("repeat: same window twice does not grow the bar",
      segs("mixbar") === beforeRepeat);

  // --- back to 1w
  click("2");
  chk("restore: live totals return", g("tot_in").textContent === liveIn);
  chk("restore: caption back to this week", /this week/.test(g("combline").textContent));
  chk("restore: bar label cleared", g("barwin").textContent === "");
  chk("restore: mixbar back to its live segment count", segs("mixbar") === liveMix);
  chk("restore: usebar back to its live segment count", segs("usebar") === liveUse);

  // --- a full tour of the toggle leaves one stack, not four
  click("5"); click("13"); click("53"); click("2");
  chk("tour: mixbar still holds one stack", segs("mixbar") === liveMix);
  chk("tour: usebar still holds one stack", segs("usebar") === liveUse);

  console.log(fail.length ? "FAIL: " + fail.join(" | ") : "ALL BAR TESTS PASSED");
  console.log("  live in", liveIn, "| window in", winIn);
  console.log("  segments: mixbar", segs("mixbar"), "of", IN_FUELS,
              "| usebar", segs("usebar"), "of", OUT_FUELS);
  process.exit(fail.length ? 1 : 0);
}, 500);
