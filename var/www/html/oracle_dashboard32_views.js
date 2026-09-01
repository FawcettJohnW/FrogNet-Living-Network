/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
/* oracle_dashboard32_views.js — [THREE_VIEWS_V1] [LOGICAL_IS_NOT_PHYSICAL_V1]
 *                               [DIRECTION_IS_NOT_SYMMETRIC_V1]
 *
 * Loads the REAL functions out of fln_dashboard32.html (no re-implementation)
 * and runs them against the Seattle chain:
 *
 *     Seattle2 -- Seattle3 -- Seattle6 -- Seattle5 -- (Internet)
 *
 * Physical: Seattle2 sees only Seattle3.
 * Logical:  Seattle2 sees every node, the distant ones marked transit.
 *
 * Run against v31 and it goes red: v31 discards transit entries at ingest.
 *
 *   node oracle_dashboard32_views.js fln_dashboard32.html
 */
const fs = require("fs");
const vm = require("vm");

const FILE = process.argv[2] || "fln_dashboard32.html";
const html = fs.readFileSync(FILE, "utf8");
const script = (html.match(/<script>([\s\S]*)<\/script>/) || [])[1];
if (!script) { console.error("FATAL: no <script> block in " + FILE); process.exit(1); }

let FAIL = 0;
const ok  = m => console.log("  ok    " + m);
const bad = m => { console.log("  FAIL  " + m); FAIL++; };

/* ---- Load the page's script in a sandbox with just enough DOM to survive
   top-level evaluation. We only call pure graph functions afterwards. ---- */
const noop = () => {};
const stubEl = new Proxy({}, {
  get: (t, k) => (k === "getContext" ? () => new Proxy({}, {get:()=>noop})
                : k === "style" || k === "dataset" ? {}
                : k === "getBoundingClientRect" ? () => ({width:1200,height:800,left:0,top:0})
                : k === "classList" ? {add:noop,remove:noop,toggle:noop,contains:()=>false}
                : typeof k === "string" && k.startsWith("on") ? null
                : noop),
  set: () => true
});
const sandbox = {
  console, Math, Date, JSON, Map, Set, Number, String, Array, Object, Boolean,
  isNaN, parseInt, parseFloat, performance: {now: () => 0},
  setTimeout: noop, setInterval: noop, clearInterval: noop, requestAnimationFrame: noop,
  fetch: () => Promise.reject(new Error("no network in oracle")),
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  getComputedStyle: () => ({getPropertyValue: () => "#888"}),
  document: {
    getElementById: () => stubEl, querySelector: () => stubEl,
    querySelectorAll: () => [], createElement: () => stubEl,
    addEventListener: noop, documentElement: stubEl, body: stubEl
  },
  window: {addEventListener: noop, devicePixelRatio: 1},
  navigator: {userAgent: "oracle"},
  ResizeObserver: function(){ return {observe: noop, unobserve: noop, disconnect: noop}; },
  MutationObserver: function(){ return {observe: noop, disconnect: noop}; },
  IntersectionObserver: function(){ return {observe: noop, disconnect: noop}; },
  URL: URL, URLSearchParams: URLSearchParams, TextEncoder: TextEncoder,
  location: {href:"http://oracle/", origin:"http://oracle", pathname:"/"},
  alert: noop, prompt: () => null, confirm: () => false
};
sandbox.window.document = sandbox.document;
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
try { vm.runInContext(script, ctx, {timeout: 10000}); }
catch (e) { console.error("FATAL: page script threw at load: " + e.message); process.exit(1); }

/* ---- Required exports ------------------------------------------------ */
for (const fn of ["unifyEdgesForDisplay", "edgeDash"]) {
  if (typeof ctx[fn] !== "function") { console.error("FATAL: " + fn + " not defined"); process.exit(1); }
}
/* Top-level let/const live in the realm lexical scope, NOT on the context
   object, so ctx.VIEW_ORDER reads undefined even when it exists. Evaluate
   in the same realm instead of reading a property. */
const evalIn = expr => { try { return vm.runInContext(expr, ctx); } catch (e) { return undefined; } };
const viewOrder = evalIn('typeof VIEW_ORDER !== "undefined" ? VIEW_ORDER : undefined');
const viewMode  = evalIn('typeof VIEW_MODE  !== "undefined" ? VIEW_MODE  : undefined');
const hasThreeViews = typeof ctx.filterDirectOnly === "function" && Array.isArray(viewOrder);

console.log("=== view model ===");
if (hasThreeViews) ok("filterDirectOnly + VIEW_ORDER exist (three views)");
else               bad("no filterDirectOnly / VIEW_ORDER \u2014 still a two-way toggle");

if (viewMode === "logical") ok('default VIEW_MODE is "logical"');
else                        bad('default VIEW_MODE is "' + viewMode + '", expected "logical"');

/* ---- The Seattle chain ---------------------------------------------- */
const S2="10.120.120.1", S3="10.130.130.1", S6="10.160.160.1", S5="10.250.250.1";
const NAME={[S2]:"Seattle2",[S3]:"Seattle3",[S6]:"Seattle6",[S5]:"Seattle5"};

/* Directed edges as ingest would build them: direct hops plus, for every
   distant peer, a transit entry naming the next hop. */
const E = new Map();
const add = (from,to,transit,via,rtt) =>
  E.set(`${from}\u2192${to}|${transit?"t":"d"}`,
        {from, to, kind:"FAST", meta:{dev:"wlan0", via:via||"", transit:!!transit, rtt_avg_ms:rtt}});

// direct hops, both directions
add(S2,S3,false,"",8);   add(S3,S2,false,"",8);
add(S3,S6,false,"",4);   add(S6,S3,false,"",4);
add(S6,S5,false,"",2);   add(S5,S6,false,"",2);
// logical routes via an intermediate
add(S2,S6,true,S3,12);   add(S6,S2,true,S3,12);
add(S2,S5,true,S3,15);   add(S5,S2,true,S6,9);    // asymmetric on purpose
add(S3,S5,true,S6,6);    add(S5,S3,true,S6,6);

const peersOf = (edges, ip) => {
  const out = new Set();
  for (const e of edges.values()) {
    if (e.kind === "EXTERNAL") continue;
    if (e.from === ip) out.add(e.to);
    else if (e.to === ip) out.add(e.from);
  }
  return [...out].map(x => NAME[x] || x).sort();
};

console.log("\n=== physical view: direct hops only ===");
if (hasThreeViews) {
  const phys = ctx.unifyEdgesForDisplay(ctx.filterDirectOnly(E), "physical");
  const p2 = peersOf(phys, S2);
  if (JSON.stringify(p2) === JSON.stringify(["Seattle3"]))
    ok("Seattle2 connects only to Seattle3 \u2014 " + JSON.stringify(p2));
  else bad("Seattle2 physical peers " + JSON.stringify(p2) + ", expected [\"Seattle3\"]");

  const p6 = peersOf(phys, S6);
  if (JSON.stringify(p6) === JSON.stringify(["Seattle3","Seattle5"]))
    ok("Seattle6 connects to Seattle3 and Seattle5 \u2014 " + JSON.stringify(p6));
  else bad("Seattle6 physical peers " + JSON.stringify(p6));

  let anyTransit = false;
  for (const e of phys.values()) if (e.meta && e.meta.transit) anyTransit = true;
  if (!anyTransit) ok("no transit edge survives into the physical view");
  else             bad("a transit edge leaked into the physical view");
} else bad("skipped \u2014 no filterDirectOnly");

console.log("\n=== logical view: every node to every other node ===");
const logi = ctx.unifyEdgesForDisplay(E, "logical");
const l2 = peersOf(logi, S2);
if (JSON.stringify(l2) === JSON.stringify(["Seattle3","Seattle5","Seattle6"]))
  ok("Seattle2 reaches every other node \u2014 " + JSON.stringify(l2));
else bad("Seattle2 logical peers " + JSON.stringify(l2) +
         ", expected all three (v31 discards transit at ingest)");

let transitKept = 0;
for (const e of logi.values()) if (e.meta && e.meta.transit) transitKept++;
if (transitKept === 6) ok("all 6 transit routes survive into the logical view");
else                   bad("only " + transitKept + " of 6 transit routes survived");

console.log("\n=== direction is preserved logically, merged physically ===");
const bothWays = (edges,a,b) => {
  let ab=false, ba=false;
  for (const e of edges.values()) {
    if (e.from===a && e.to===b) ab=true;
    if (e.from===b && e.to===a) ba=true;
  }
  return ab && ba;
};
if (bothWays(logi,S2,S3)) ok("logical keeps A\u2192B and B\u2192A as separate edges");
else                      bad("logical merged the two directions");

if (hasThreeViews) {
  const phys = ctx.unifyEdgesForDisplay(ctx.filterDirectOnly(E), "physical");
  if (!bothWays(phys,S2,S3)) ok("physical merges the pair into one line");
  else                       bad("physical kept both directions");
}

console.log("\n=== a route through a host is not drawn as a wire ===");
const dDirect  = ctx.edgeDash({kind:"FAST", meta:{dev:"wlan0"}});
const dTransit = ctx.edgeDash({kind:"FAST", meta:{dev:"wlan0", transit:true}});
const dSynth   = ctx.edgeDash({kind:"EXTERNAL", meta:{dev:"uplink", synthetic:true}});
const dUplink  = ctx.edgeDash({kind:"EXTERNAL", meta:{dev:"uplink"}});

if (JSON.stringify(dTransit) !== JSON.stringify(dDirect))
  ok("transit dash differs from direct \u2014 " + JSON.stringify(dTransit));
else bad("transit route drawn identically to a direct hop");

if (JSON.stringify(dSynth) !== JSON.stringify(dUplink))
  ok("inferred egress hop differs from observed \u2014 " + JSON.stringify(dSynth));
else bad("BFS-synthesized hop drawn identically to an observed one");

console.log();
if (FAIL) { console.log(`FAILED ${FAIL} check(s)`); process.exit(1); }
console.log("PASS \u2014 [THREE_VIEWS_V1] [LOGICAL_IS_NOT_PHYSICAL_V1] [DIRECTION_IS_NOT_SYMMETRIC_V1]");
