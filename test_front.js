// test_front.js - the frontispiece.
//
// The frontispiece is the first thing a reader sees and the last thing
// anyone thinks to check, so these assertions guard the two ways it can
// quietly go wrong: drifting out of agreement with the panel it summarises,
// and following the trend selector when it must not.
//
//   node test_front.js      (needs docs/index.html, docs/data.json, jsdom)

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
  const doc = w.document;
  const g = id => doc.getElementById(id);
  const fail = [];
  const chk = (name, cond, detail) => {
    if (cond) { console.log("  ok   " + name); }
    else { fail.push(name); console.log("  FAIL " + name +
                                        (detail ? " - " + detail : "")); }
  };
  const pts = [...doc.querySelectorAll(".pt")];
  const num = s => parseFloat(String(s).replace(/[^0-9.\-]/g, ""));

  console.log("frontispiece");

  // --- masthead
  chk("brand reads UK Heat Shift",
      /UK Heat\s+Shift/.test(doc.querySelector(".brand").textContent),
      doc.querySelector(".brand").textContent.trim());
  chk("only Shift is italic",
      doc.querySelector(".brand i").textContent.trim() === "Shift");
  chk("standfirst present and full width",
      /strategic case for GW-scale geothermal heating and cooling in the UK/i
        .test(doc.querySelector(".standfirst")
                 .textContent.replace(/\s+/g, " ").trim()),
      doc.querySelector(".standfirst").textContent.replace(/\s+/g, " ").trim());
  chk("standfirst is outside the flex row, so it spans the page",
      doc.querySelector(".standfirst").closest(".tickbox") === null &&
      doc.querySelector(".standfirst").closest(".brandbox") === null);

  // --- the six points
  chk("panel is shown", g("front").hidden === false);
  chk("six points render", pts.length === 6, pts.length + " rendered");
  chk("numbered 01 to 06 in order",
      pts.map(p => p.querySelector(".n").textContent).join(",") ===
      "01,02,03,04,05,06");
  chk("every point has a figure, a claim and a qualification",
      pts.every(p => p.querySelector(".fig").textContent.trim() &&
                     p.querySelector(".claim").textContent.trim() &&
                     p.querySelector(".qual").textContent.trim()));

  // --- every evidence link must resolve to something on the page. A dead
  // anchor is invisible until a reader clicks it and nothing happens.
  const dead = pts.map(p => p.querySelector(".ev").getAttribute("href"))
                  .filter(h => !h || !doc.querySelector(h));
  chk("no dead evidence anchors", dead.length === 0, dead.join(", "));

  // --- point 05 must equal the capital panel, not a copy of it
  const cap = w.capitalModel && w.capitalModel.compute();
  chk("capital panel initialised", !!cap);
  if (cap) {
    const p5 = pts.find(p => p.querySelector(".n").textContent === "05");
    chk("point 05 coverage equals the panel's published default",
        num(p5.querySelector(".fig").textContent) === Math.round(cap.cov),
        "front " + p5.querySelector(".fig").textContent +
        " vs panel " + Math.round(cap.cov) + "%");
    chk("point 05 quotes the panel's build and gap",
        p5.querySelector(".qual").textContent.includes(
          String(Math.round(cap.buildBn))) &&
        p5.querySelector(".qual").textContent.includes(
          String(Math.round(cap.gapBn))));
    chk("point 05 is marked as the figure that argues against the case",
        p5.querySelector(".fig").classList.contains("warn"));
    // "most" was wrong at 39%. Whatever the wording, it must not overclaim
    // while the panel it quotes reads below half.
    chk("point 05 does not claim 'most' below 50% coverage",
        cap.cov >= 50 || !/\bmost\b/i.test(p5.querySelector(".claim").textContent),
        p5.querySelector(".claim").textContent);
    chk("point 05 explains why the gap does not close by waiting",
        /does not close by waiting|does not get cheaper/i
          .test(p5.querySelector(".qual").textContent));
    chk("point 05 names the share that cannot learn",
        p5.querySelector(".qual").textContent
          .includes(String(Math.round(cap.adderShare))));
  }

  // --- the frontispiece is annual and must NOT follow the trend selector.
  // In an August week the saving is near zero; a strategic case that
  // restated to it would be worse than useless.
  const before = pts.map(p => p.querySelector(".fig").textContent);
  const ctl = g("trendctl");
  const btn = w2 => [...ctl.querySelectorAll("button")]
                      .find(b => b.dataset.win === w2);
  btn("2").dispatchEvent(new w.MouseEvent("click", {bubbles: true}));
  const after = [...doc.querySelectorAll(".pt")]
                  .map(p => p.querySelector(".fig").textContent);
  chk("points do not move with the trend selector",
      JSON.stringify(before) === JSON.stringify(after));
  chk("footnote says the case is pinned to twelve months",
      /pinned to twelve months/i.test(g("frontfoot").textContent));

  // --- point 01 is a twelve-month total, not a week
  const yr = data.history.filter(e => e && e.whatif)
               .sort((a,b) => a.week_ending < b.week_ending ? -1 : 1)
               .slice(-52);
  const purTWh = yr.reduce((a,e) => a + e.purchased_GWh, 0) / 1000;
  chk("point 01 is the 12-month purchased total",
      Math.abs(num(pts[0].querySelector(".fig").textContent) - purTWh) < 1.5,
      "front " + pts[0].querySelector(".fig").textContent +
      " vs " + purTWh.toFixed(1) + " TWh");

  // --- the starting-point heading names the window, and the toggle sits
  // under the heading rather than under the figures it now labels
  const WIN = {"2":"in the last week", "5":"in the last 4 weeks",
               "13":"in the last 12 weeks", "53":"in the last 12 months"};
  chk("heading reads as the starting point",
      /starting point/i.test(g("startline").textContent));
  Object.entries(WIN).forEach(([k, txt]) => {
    btn(k).dispatchEvent(new w.MouseEvent("click", {bubbles: true}));
    chk("window " + k + " names itself: " + txt,
        g("startwin").textContent.trim() === txt,
        "got " + g("startwin").textContent);
  });
  chk("toggle sits above the headline figures",
      !!(ctl.compareDocumentPosition(g("st_energy")) &
         w.Node.DOCUMENT_POSITION_FOLLOWING));
  chk("only one trend control on the page",
      doc.querySelectorAll("#trendctl").length === 1);

  // --- the floating back-to nav
  const nav = g("backnav");
  const navb = nav ? [...nav.querySelectorAll("button")] : [];
  chk("back-to nav exists with two buttons", navb.length === 2,
      navb.length + " buttons");
  chk("nav has an accessible name", !!(nav && nav.getAttribute("aria-label")));
  chk("both nav targets resolve",
      navb.every(b => !!g(b.dataset.to)),
      navb.map(b => b.dataset.to).join(", "));
  chk("nav points at the case and the starting point",
      navb.map(b => b.dataset.to).join(",") === "front,startline");
  // Scroll moves the viewport, not the keyboard. Without tabindex the
  // targets cannot take focus and a keyboard user is left in the footer.
  chk("both targets can take focus",
      navb.every(b => g(b.dataset.to).getAttribute("tabindex") === "-1"));
  chk("nav is hidden until the reader scrolls past the case",
      !nav.classList.contains("show"));
  if (typeof w.backnavTick === "function") {
    g("front").getBoundingClientRect = () => ({bottom: -40, top: -300});
    w.backnavTick();
    chk("nav appears once the case has scrolled away",
        nav.classList.contains("show"));
    g("front").getBoundingClientRect = () => ({bottom: 300, top: 20});
    w.backnavTick();
    chk("nav hides again at the top", !nav.classList.contains("show"));
  }
  const jumped = [];
  w.Element.prototype.scrollIntoView = function(){ jumped.push(this.id); };
  navb.forEach(b => b.dispatchEvent(new w.MouseEvent("click", {bubbles: true})));
  chk("each button scrolls to its own target",
      jumped.join(",") === "front,startline", jumped.join(","));

  console.log(fail.length ? "\n" + fail.length + " FAILED" : "\nall passed");
  process.exit(fail.length ? 1 : 0);
}, 600);
