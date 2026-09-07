/**
 * Self-check for web/board.html.
 *
 *     python run.py serve            # in one terminal
 *     node web/board.check.mjs       # in another
 *
 * The board makes one claim that is worth a test: every figure it shows came
 * out of an API payload, and nothing was computed on the way to the screen. A
 * screenshot cannot prove that -- a plausible wrong number looks exactly like a
 * right one -- so this loads the REAL render functions out of board.html (not a
 * copy of them), runs them against the live API, and asserts over the HTML they
 * produce.
 *
 * It needs no npm packages and no browser: the page's DOM use is narrow enough
 * to stub, which is itself a property worth keeping.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.ANTE_API || "http://127.0.0.1:8000";

/* ------------------------------------------------------------------ stubs */

/** Enough of an element for the board's renderers; it records what they wrote. */
function node() {
  const el = {
    innerHTML: "", textContent: "", value: "", disabled: false,
    style: {}, dataset: {}, children: [], files: [],
    addEventListener() {}, setAttribute() {}, classList: { add() {}, remove() {} },
    querySelector: () => node(), querySelectorAll: () => [],
    closest: () => null, appendChild() {},
  };
  return el;
}

const nodes = new Map();
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  },
  addEventListener() {},
  querySelectorAll: () => [],
};

/* ------------------------------------------------- load the real functions */

const html = fs.readFileSync(path.join(HERE, "board.html"), "utf8");
// tolerate CRLF: the file is edited on Windows as often as not
const m = html.match(/<script>\r?\n([\s\S]*?)<\/script>/);
assert.ok(m, "board.html has no inline <script> -- has the file been rewritten?");
const src = m[1].replace(/\bboot\(\);\s*$/, "");

const ctx = {
  document: doc,
  location: { protocol: "http:", origin: BASE },
  navigator: {},
  console,
  fetch: (u, o) => fetch(u.startsWith("http") ? u : BASE + u, o),
  setTimeout,
  URL,
};
vm.createContext(ctx);
vm.runInContext(
  src + "\n;globalThis.__board = { renderHead, renderBanner, renderTimeline, renderFindings,"
      + " renderCurator, consequence, figureOf, day, money, plain, prose, S };",
  ctx);
const B = ctx.__board;

/* ------------------------------------------------------------- the payloads */

const get = async (p) => {
  const r = await fetch(BASE + p);
  assert.ok(r.ok, `${p} -> ${r.status}. Is \`python run.py serve\` running?`);
  return r.json();
};

const findings = await get("/api/findings");
const rules = await get("/api/rules");
const alerts = await get("/api/alerts");

B.S.findings = findings;
B.S.rules = rules;
B.S.alerts = alerts;
for (const r of rules.rules) B.S.by[r.path] = r;

B.renderHead(); B.renderBanner(); B.renderTimeline(); B.renderFindings(); B.renderCurator();

const painted = [...nodes.values()]
  .map((e) => (e.innerHTML || "") + " " + (e.textContent || "")).join("\n");

/* ------------------------------------------------------------- assertions */

// 1. nothing rendered as a placeholder or a broken join
for (const bad of ["undefined", "NaN", "[object Object]", "null/", "Infinity"]) {
  assert.ok(!painted.includes(bad), `the board rendered "${bad}" somewhere`);
}

// 2. every rule and every affected party reached the screen
for (const r of findings.rules) {
  assert.ok(painted.includes(r.title), `rule missing from the board: ${r.title}`);
  for (const a of r.affected) {
    assert.ok(painted.includes(a.who), `affected party missing from the board: ${a.who}`);
  }
}

// 3. THE INVARIANT. Every money figure on the page is a number the API
//    returned -- not a sum, a rate, or a conversion the browser worked out.
const allowed = new Set();
const permit = (n) => {
  if (typeof n === "number" && Number.isFinite(n)) allowed.add(B.money(n));
};
permit(findings.annual_gap_total);
for (const r of findings.rules) {
  permit(r.threshold_before); permit(r.threshold_after);
  for (const a of r.affected) { permit(a.value); permit(a.annual_gap); }
}
// figures quoted inside a human-written `# Cost` note are the note's own words,
// and are reproduced verbatim rather than derived -- so they are allowed too
for (const r of rules.rules) {
  for (const s of String(r.cost || "").concat(" ", r.next_step || "").match(/S\$\d{1,3}(?:,\d{3})*\b/g) || [])
    allowed.add(s);
}
// as are the ones the rule titles print
for (const r of findings.rules) {
  for (const s of (r.title.match(/S\$\d{1,3}(?:,\d{3})*\b/g) || [])) allowed.add(s);
}

// anchored so a figure followed by a comma in prose does not swallow it
const shown = new Set(painted.match(/S\$\d{1,3}(?:,\d{3})*\b/g) || []);
const invented = [...shown].filter((s) => !allowed.has(s));
assert.deepEqual(invented, [],
  `the board shows ${invented.length} figure(s) the API never returned: ${invented.join(", ")}`);

// 4. dates are rendered for people, not as ISO stamps, in the findings list
const list = nodes.get("findings").innerHTML;
const iso = list.match(/\b\d{4}-\d{2}-\d{2}\b/g) || [];
assert.deepEqual(iso, [], `raw ISO dates leaked into the findings list: ${iso.join(", ")}`);

// 5. the refusals panel reports what the vault actually holds, and never
//    invents a diff when no change was applied
const openAlerts = (alerts.alerts || []).length;
const curator = nodes.get("curator").innerHTML;
if (openAlerts) {
  assert.ok(/refused/i.test(curator), "an open alert exists but the board does not show it");
} else {
  assert.ok(/clean|quiet/i.test(curator), "no alerts, but the board did not say the sweep was clean");
}

// 6. markdown from the vault is read back as prose, never as raw syntax
assert.ok(!/\]\(rules\//.test(painted), "an Obsidian link reached the screen unrendered");
assert.ok(!/\|---/.test(painted), "a markdown table reached the screen unrendered");

console.log(`board ok     ->  ${findings.rules.length} rules, ${findings.parties} parties, `
  + `${shown.size} distinct figures on screen, every one traced to a payload`);
console.log(`             ->  ${openAlerts} alert(s) shown, no ISO dates, no raw markdown`);
