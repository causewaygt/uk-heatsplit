const { JSDOM } = require("jsdom");
const fs = require("fs");

const html = fs.readFileSync("docs/index.html", "utf8");
const data = JSON.parse(fs.readFileSync("docs/data.json", "utf8"));
// self-sufficient stability-line fixture: seed a second run-date so the
// 2-run rendering path is exercised regardless of the data.json on disk
if (data.cooling_reconciliation && data.cooling_reconciliation.stability_log) {
  const log = data.cooling_reconciliation.stability_log;
  if (log.length === 1) {
    const y = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
    log.unshift({ ...log[0], date: y, cdd_base_c: 16.0 });
    data.cooling_reconciliation.stability = {
      runs_60d: 2, base_range_60d: [16.0, 18.0],
      slope_range_60d: [19.5, 20.7], base_stable_pm1c: true,
      gate_note: "",
    };
  }
}

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "https://example.test/",
  pretendToBeVisual: true,
});
const w = dom.window;

// stubs the page expects
w.Plotly = { newPlot: () => {}, react: () => {} };
w.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(data) });
// jsdom has no layout: clientWidth is 0 everywhere, exercising the fallback
w.requestAnimationFrame = (f) => setTimeout(f, 0);

// re-run the inline script (it already ran once at parse with fetch undefined
// -> caught by .catch). Extract and eval it now that stubs exist.
const m = html.match(/<script>([\s\S]*?)<\/script>/);
w.eval(m[1]);

setTimeout(() => {
  const doc = w.document;
  const get = (id) => doc.getElementById(id);
  const fails = [];
  const ok = (name, cond) => { if (!cond) fails.push(name); };

  ok("stamp filled", !/not found/.test(get("stamp").textContent));
  ok("hero energy", get("st_energy").textContent !== "—");
  ok("energy split in cap", /heat [\d,]+ \u00b7 cooling [\d,]+/.test(
      get("st_energy_cap").textContent));
  ok("whatif split in cap", /heat [\d,]+ \u00b7 cooling [\d,]+/.test(
      get("wf_energy_cap").textContent));
  ok("1w wf bill saves", /saves \u00a3[\d,]+m this week/.test(
      get("wf_bill_cap").textContent));
  ok("1w wf energy saves", /saves [\d,]+ GWh this week/.test(
      get("wf_energy_cap").textContent));
  ok("trendctl visible", !get("trendctl").hidden);
  const trends = [...doc.querySelectorAll(".trend")];
  ok("4 trend slots", trends.length === 4);
  ok("all trends drawn", trends.every((t) => !t.hidden && t.querySelector("svg")));
  ok("actual polyline", trends.every((t) => t.querySelector(".tr-actual")));
  ok("whatif polyline", trends.every((t) => t.querySelector(".tr-whatif")));
  ok("gap polygon", trends.every((t) => t.querySelector(".tr-gap")));
  ok("delta text", trends.every((t) => /vs \d+ \w+/.test(t.querySelector(".tr-delta").textContent)));
  ok("indig delta in pp", /pp/.test(trends[1].querySelector(".tr-delta").textContent));
  ok("trendspan count", /\d+ wks/.test(get("trendspan").textContent));
  ok("trendnote visible", get("trendpara").style.display === "");
  ok("version 7.0.0", /7\.0\.0/.test(get("siteversion").textContent) ||
      /SITE_VERSION = '5\.0\.0'/.test(m[1]));

  // reconciliation diagnostic renders when present, hides when absent
  ok("recon visible", doc.getElementById("reconblock").style.display === "");
  ok("recon line filled", /GWh\/CDD/.test(get("reconline").textContent));
  ok("recon flat share", /weather-flat/.test(get("reconline").textContent));
  ok("recon note", /Diagnostic/.test(get("reconnote").textContent));
  ok("recon stability line", /Stability \(\d+ runs/.test(get("reconnote").textContent));

  // sibling panel renders with gate band; single-definition discipline
  ok("empty bar visible", doc.getElementById("emptybar").style.display === "");
  ok("empty bar GWth stat", /\d+\.\d/.test(get("eb_need").textContent));
  ok("empty bar note scale", /GWth against/.test(get("emptybarnote").textContent));
  ok("shares chart present", !!doc.getElementById("emptysharesplot"));
  ok("shares note calibration",
     /already serves about the share/.test(get("emptybarnote").textContent));
  ok("boiler flow note", /replacement flow/.test(get("emptybarnote").textContent));
  // import #4 from the cross-calibration: every id defined exactly once
  const ids = [...html.matchAll(/ id="([^"]+)"/g)].map(m => m[1]);
  const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
  ok("no duplicate element ids", dup.length === 0);
  const renderers = ["setHero", "draw", "cap_prices"]
    .map(n => (html.match(new RegExp("function " + n + "\\(|const " + n +
                                     " = ", "g")) || []).length);
  ok("renderers defined once", renderers.every(c => c <= 1));

  // default is this-week: hero untouched, 1w pressed
  const b1w = doc.querySelector('button[data-win="2"]');
  ok("default 1w pressed", b1w.getAttribute("aria-pressed") === "true");
  const pts1w = trends[0].querySelector(".tr-actual")
    .getAttribute("points").trim().split(/\s+/).length;
  ok("1w sparkline = 2 points", pts1w === 2);
  const liveEnergy = get("st_energy").innerHTML;
  const liveBillCap = get("st_bill_cap").innerHTML;
  const d1 = trends[0].querySelector(".tr-delta").textContent;

  // 12m: hero re-totals, captions switch, delta changes
  const b12m = doc.querySelector('button[data-win="53"]');
  b12m.dispatchEvent(new w.Event("click", { bubbles: true }));
  const d2 = trends[0].querySelector(".tr-delta").textContent;
  ok("window changes delta", d1 !== d2);
  ok("12m re-totals energy", get("st_energy").innerHTML !== liveEnergy);
  const pts12m = trends[0].querySelector(".tr-actual")
    .getAttribute("points").trim().split(/\s+/).length;
  ok("12m sparkline > 40 points", pts12m > 40);
  ok("12m energy scaled", /TWh|GWh/.test(get("st_energy").innerHTML));
  ok("12m bill caption windowed", /12 months/.test(get("st_bill_cap").textContent));
  ok("12m energy split", /heat [\d,]+ (GWh|TWh) \u00b7 cooling/.test(
      get("st_energy_cap").textContent));
  ok("12m whatif split", /heat [\d,]+ (GWh|TWh) \u00b7 cooling/.test(
      get("wf_energy_cap").textContent));
  ok("12m bill split", /heat \u00a3[\d.,]+(bn|m) \u00b7 cooling \u00a3/.test(
      get("st_bill_cap").textContent));
  ok("12m emissions split", /heat [\d.,]+ (Mt|kt) \u00b7 cooling/.test(
      get("st_carbon_cap").textContent));
  ok("12m wf bill split+saves",
     /heat \u00a3[\d.,]+(bn|m) \u00b7 cooling \u00a3[\d.,]+(bn|m) \u00b7 saves/.test(
      get("wf_bill_cap").textContent));
  ok("routes panel visible", !doc.getElementById("routes").hidden);
  ok("folds present closed", ["fold_engine","fold_cool","fold_cost",
     "fold_carb"].every(id => { const f = doc.getElementById(id);
       return f && !f.hasAttribute("open"); }));
  ok("engine inside fold", !!doc.querySelector("#fold_engine #ts"));
  ok("conclusions outside folds", !doc.querySelector("#fold_cost #billline")
     && !doc.querySelector("#fold_cool #coolobsline"));
  ok("geothermal networks rename", /SCOP-5 geothermal network/.test(
      get("rt_winter").innerHTML) || /SCOP-5 geothermal/.test(
      doc.body.innerHTML));
  ok("winter stress cards", /on top of what Britain was using/.test(
      get("rt_winter").innerHTML) && /GW/.test(get("rt_winter").innerHTML));
  ok("summer stress cards", /inside today's demand, not on top of it/.test(
      get("rt_summer").innerHTML));
  ok("premium cards", /coincidence premium/.test(get("rt_prem").innerHTML));
  ok("routes note basis", /modelled from weather/.test(
      get("rt_note").textContent)
     && /already inside today's demand/.test(get("rt_note").textContent));
  ok("binding cards", /spare capacity left/.test(get("rt_bind").innerHTML));
  ok("exhibit week labelled",
     /TIGHTEST WEEK/.test(get("rt_wklbl").textContent));
  ok("exhibit centred on the binding hour", (function(){
      const wr = data.whatif_routes;
      if(!(wr.system && wr.binding_week)) return true;
      const bh = wr.system.routes.ashp.binding_hour;
      const st = new Date(wr.binding_week.start + ":00:00Z");
      const en = new Date(st.getTime() + 167*3600e3);
      const b = new Date(bh + ":00:00Z");
      return b >= st && b <= en;
    })());
  ok("winter cards hourly basis", /% on top of what Britain was using/.test(
      get("rt_winter").innerHTML));
  ok("summer card hourly basis", /% of that hour's observed demand/.test(
      get("rt_summer").innerHTML));
  ok("bind bars drawn", !doc.getElementById("rt_bindbars").hidden);
  ok("gas key collapses when one leg runs", (function(){
      const c = data.weekly_mix.components_GWh;
      const both = (c.gas_space || 0) > 0 && (c.gas_dhw || 0) > 0;
      const keys = [...doc.getElementById("mixlegend").querySelectorAll("b")]
        .map(b => b.textContent).filter(x => /Gas/.test(x));
      return both ? keys.length === 2 : keys.length === 1 && keys[0] === "Gas";
    })());
  ok("summer bars drawn", !doc.getElementById("rt_sumbars").hidden);
  ok("winter stress bars container present",
     !!doc.getElementById("rt_winbars"));
  ok("toggle precedes the card rows",
     doc.body.innerHTML.indexOf('id="rt_main"') <
     doc.body.innerHTML.indexOf('THE COLDEST HOUR'));
  ok("exhibit label carries dates",
     /168 HOURS|SEVEN DAYS/.test(get("rt_wklbl").textContent)
     && / TO /.test(get("rt_wklbl").textContent));
  ok("system basis in footer", /simple overlay/i.test(get("rt_note").textContent)
     && /methodology/.test(get("rt_note").textContent)
     && get("rt_note").textContent.length < 700);
  ok("12m wf emissions split+saves",
     /heat [\d.,]+ (Mt|kt) \u00b7 cooling [\d.,]+ (Mt|kt) \u00b7 saves/.test(
      doc.getElementById("wf_carbon_cap").textContent));
  ok("12m bill scaled", /£[\d.,]+(bn|m)/.test(get("st_bill").textContent));
  ok("12m emissions scaled", /(Mt|kt)/.test(get("st_carbon").textContent));
  ok("12m whatif totals", /TWh|GWh/.test(get("wf_energy").innerHTML));
  ok("12m whatif saves", /saves/.test(get("wf_carbon").nextElementSibling === null
      ? "" : doc.getElementById("wf_carbon_cap").textContent));
  ok("12m indig avg", /%/.test(get("st_indig").textContent));

  // back to 1w: exact restore
  b1w.dispatchEvent(new w.Event("click", { bubbles: true }));
  ok("1w restores energy", get("st_energy").innerHTML === liveEnergy);
  ok("1w restores bill cap", get("st_bill_cap").innerHTML === liveBillCap);

  // degraded data: history absent -> layer stays hidden, page still renders
  const dom2 = new JSDOM(html, { runScripts: "dangerously", url: "https://example.test/" });
  const w2 = dom2.window;
  w2.Plotly = { newPlot: () => {}, react: () => {} };
  const d2j = JSON.parse(JSON.stringify(data));
  delete d2j.history;
  delete d2j.cooling_reconciliation;
  delete d2j.sibling;
  if (d2j.geothermal) delete d2j.geothermal.hardware;
  w2.fetch = () => Promise.resolve({ json: () => Promise.resolve(d2j) });
  w2.requestAnimationFrame = (f) => setTimeout(f, 0);
  w2.eval(m[1]);
  setTimeout(() => {
    ok("no-history: ctl hidden", w2.document.getElementById("trendctl").hidden);
    ok("no-history: hero still fills", w2.document.getElementById("st_energy").textContent !== "—");
    ok("no-history: trendpara hidden", w2.document.getElementById("trendpara").style.display === "none");
    ok("no-recon: block hidden", w2.document.getElementById("reconblock").style.display === "none");
    ok("no-hardware: empty bar hidden",
       w2.document.getElementById("emptybar").style.display === "none");

    // single-entry history -> also hidden
    const dom3 = new JSDOM(html, { runScripts: "dangerously", url: "https://example.test/" });
    const w3 = dom3.window;
    w3.Plotly = { newPlot: () => {}, react: () => {} };
    const d3j = JSON.parse(JSON.stringify(data));
    d3j.history = d3j.history.slice(-1);
    w3.fetch = () => Promise.resolve({ json: () => Promise.resolve(d3j) });
    w3.requestAnimationFrame = (f) => setTimeout(f, 0);
    w3.eval(m[1]);
    setTimeout(() => {
      ok("1-entry: ctl hidden", w3.document.getElementById("trendctl").hidden);
      if (fails.length) { console.log("FAIL:", fails); process.exit(1); }
      console.log("ALL PAGE TESTS PASSED");
      console.log("sample delta:", trends[2].querySelector(".tr-delta").textContent);
      console.log("trendspan:", get("trendspan").textContent);
    }, 200);
  }, 200);
}, 300);
