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
const raw = html;
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
  // STRUCTURAL GATE. `node --check` on the extracted script proves the script
  // parses - it does NOT prove the file is well formed. A failed string
  // replacement once wrote 1,341 characters of JavaScript in front of the
  // doctype, which rendered as visible text at the top of the live page while
  // every syntax check passed. Check the shape of the document itself.
  chk("nothing before the doctype",
      /^\s*<!DOCTYPE html>/i.test(raw),
      JSON.stringify(raw.slice(0, 60)));
  // Count ALL script openers, including <script src=...> and <script data-...>,
  // not just the bare form - three tags here: plotly, the inline block, and
  // the analytics tag.
  chk("script tags balance",
      (raw.match(/<script\b/g) || []).length ===
      (raw.match(/<\/script>/g) || []).length,
      "open " + (raw.match(/<script\b/g) || []).length +
      " close " + (raw.match(/<\/script>/g) || []).length);
  chk("exactly one inline script block",
      (raw.match(/<script>/g) || []).length === 1);
  chk("no javascript leaking into the markup",
      !/^[^<]*key\(css\(/m.test(raw.split("<body>")[0]));

  chk("brand reads UK Heat Split",
      /UK Heat\s+Split/.test(doc.querySelector(".brand").textContent),
      doc.querySelector(".brand").textContent.trim());
  chk("only Split is italic",
      doc.querySelector(".brand i").textContent.trim() === "Split");
  chk("standfirst present and full width",
      /A live estimate of the energy behind Britain\u2019s building heat and cooling/i
        .test(doc.querySelector(".standfirst")
                 .textContent.replace(/\s+/g, " ").trim()),
      doc.querySelector(".standfirst").textContent.replace(/\s+/g, " ").trim());
  chk("standfirst is outside the flex row, so it spans the page",
      doc.querySelector(".standfirst").closest(".tickbox") === null &&
      doc.querySelector(".standfirst").closest(".brandbox") === null);

  // --- the six points
  chk("panel is shown", g("front").hidden === false);
  chk("six points render", pts.length === 6, pts.length + " rendered");
  chk("the numbered points run 01 to 05 in order",
      pts.map(p => { const n = p.querySelector(".n"); return n ? n.textContent : "-"; })
         .join(",").startsWith("01,02,03,04,05"));
  chk("every uncovered point has a figure, a claim and a qualification",
      pts.filter(p => !p.classList.contains("undercon"))
         .every(p => p.querySelector(".fig").textContent.trim() &&
                     p.querySelector(".claim").textContent.trim() &&
                     p.querySelector(".qual").textContent.trim()));

  // --- every evidence link must resolve to something on the page. A dead
  // anchor is invisible until a reader clicks it and nothing happens.
  // A held point carries no evidence link - it would point at a covered
  // panel with nothing on it - so only check the links that exist.
  const dead = pts.map(p => p.querySelector(".ev"))
                  .filter(a => a)
                  .map(a => a.getAttribute("href"))
                  .filter(h => !h || !doc.querySelector(h));
  chk("no dead evidence anchors", dead.length === 0, dead.join(", "));

  // --- the value point and its panel are HELD behind one flag. Both are
  // covered or neither is: a summary claiming a number the panel no longer
  // shows is the exact failure these tests exist to catch.
  // The panel and the point that reads it are held together by one flag. What
  // this guards is that they are never in different states: a live point over
  // a covered panel, or a figure quoted from a model that is not running.
  const cap = w.capitalModel ? w.capitalModel.compute() : null;
  const p6 = pts[pts.length - 1];
  const panelHeld = !!g("capitalpanel").querySelector(".undercon");
  chk("panel and frontispiece point are held together",
      (cap === null) === panelHeld &&
      p6.classList.contains("undercon") === panelHeld,
      "model " + (cap ? "on" : "off") + ", panel " +
      (panelHeld ? "covered" : "live"));
  if (panelHeld) {
    chk("the held point is labelled work in progress",
        /work in progress/i.test(p6.textContent));
    chk("the held point says nothing about the work itself",
        !p6.querySelector("p"));
    chk("no figure is left in the held point",
        !p6.querySelector(".fig"));
  } else {
    chk("point 06 ratio equals the panel's own",
        p6.querySelector(".fig").textContent.trim()
          .startsWith(cap.bcr.toFixed(2)),
        p6.querySelector(".fig").textContent.trim() + " vs " + cap.bcr.toFixed(2));
    chk("point 06 links to the panel",
        p6.querySelector(".ev").getAttribute("href") === "#capitalpanel");
    chk("point 06 claim tracks whether it clears",
        cap.bcr >= 1 ? /pays for itself\./.test(p6.textContent)
                     : /does not yet pay/.test(p6.textContent));
  }
  // --- point 06 closes the case
  const pOut = pts.find(p => p.querySelector(".n").textContent === "05");
  // The claim sentence must track its own figure. It read "fit inside" while
  // the limit showed 98%, which is the failure this guards against.
  const p3 = pts.find(p => { const n = p.querySelector(".n");
                             return n && n.textContent === "03"; });
  if (p3) {
    const share = num(p3.querySelector(".fig").textContent);
    const cl = p3.querySelector(".claim").textContent;
    chk("point 03 claim matches its own figure",
        share >= 100 ? /fit inside the grid/.test(cl)
                     : /come close to fitting/.test(cl),
        share + "% -> " + cl);
    chk("point 03 states the no-peaking assumption",
        /gas peaking/i.test(p3.querySelector(".qual").textContent));
  }

  chk("the outlier point carries the closing line",
      /diffusion challenge, not an innovation deficit/i
        .test(pOut.querySelector(".qual").textContent));
  chk("the closing line runs on inside the paragraph, not set apart",
      !pOut.querySelector(".qual .close") &&
      /there\. All the technology is proven/.test(
        pOut.querySelector(".qual").textContent));

  // --- the frontispiece is annual and must NOT follow the trend selector.
  // In an August week the saving is near zero; a strategic case that
  // restated to it would be worse than useless.
  const before = pts.map(p => { const f = p.querySelector(".fig"); return f ? f.textContent : "held"; });
  const ctl = g("trendctl");
  const btn = w2 => [...ctl.querySelectorAll("button")]
                      .find(b => b.dataset.win === w2);
  btn("2").dispatchEvent(new w.MouseEvent("click", {bubbles: true}));
  const after = [...doc.querySelectorAll(".pt")]
                  .map(p => { const f = p.querySelector(".fig"); return f ? f.textContent : "held"; });
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

  // --- point 01's combustion share must be annual, like everything beside
  // it. This week's share is materially different (76% in August against
  // 92% over the year) and mixing the two inside one point is a trap.
  const COMB = ['gas_space','gas_dhw','oil','bio_other','solid','heat_networks'];
  let cIn = 0, tIn = 0;
  yr.filter(e => e.fuels).forEach(e => {
    for (const k in e.fuels) {
      if (k === '_u') continue;
      tIn += e.fuels[k].i;
      if (COMB.indexOf(k) >= 0) cIn += e.fuels[k].i;
    }
  });
  const annualComb = Math.round(100 * cIn / tIn);
  chk("point 01 combustion share is annual, not this week's",
      pts[0].querySelector(".qual").textContent
        .includes(annualComb + "% of the energy"),
      "expected " + annualComb + "%, weekly is " +
      Math.round(100 * data.weekly_mix.combustion_share) + "%");

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
