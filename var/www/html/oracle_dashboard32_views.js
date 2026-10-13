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
/* oracle_dashboard32_views.js — [MAP_FROM_THE_SNAPSHOT_V1 - T5b] [ONE_MONITOR_TRUTH_V1]
 *
 * Loads the REAL script out of fln_dashboard32.html (no re-implementation) and runs its buildFromSources() against a
 * fake server: getSnapshot.php and api.php answer what each case says. The map must be the snapshot and nothing else:
 *   - region: one box per member, the forward pond, a hub link from each alive member to it (this node's labelled
 *     with its handshake age), the databasehost from the elections; a member that is down is a box with no hub link
 *   - the sensors carry an echo with upstream addresses and Topology routes to the Internet: no Internet node, no
 *     uplink, no edge on the map; measured hops reach only the Network Links panel
 *   - LAN-only: the members and the databasehost, no pond, no hub link
 *   - getSnapshot.php fails: everything is cleared (nothing from the last picture), the header, the status and the
 *     ponds say "getSnapshot.php failed at HH:MM:SS: <why>"; the next refresh draws again
 *   - api.php fails: the map stands (it is the snapshot's), the panels say what failed and when
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
const check = (c, m, why) => c ? ok(m) : bad(m + (why !== undefined ? " \u2014 " + why : ""));

/* ---- a DOM that keeps what is written into it ---- */
const noop = () => {};
const ctx2d = new Proxy({}, {get: (t, k) => k in t ? t[k] : (k === "measureText" ? () => ({width: 60}) : noop),
                             set: (t, k, v) => { t[k] = v; return true; }});
const els = {};
const mk = () => ({
  children: [], hidden: false, href: "", className: "", _t: "", style: {}, title: "", onclick: null, onchange: null, value: "2",
  append(...xs){ for (const x of xs) this.children.push(x); }, appendChild(x){ this.children.push(x); },
  set textContent(v){ this._t = String(v); this.children = []; },
  get textContent(){ return this._t + this.children.map(c => c.textContent ?? c.data ?? "").join(""); },
  set innerHTML(v){ this._t = String(v).replace(/<[^>]*>/g, ""); this.children = []; }, get innerHTML(){ return this._t; },
  getContext: () => ctx2d, getBoundingClientRect: () => ({width: 1200, height: 800, left: 0, top: 0}),
  addEventListener: noop, classList: {add: noop, remove: noop, toggle: noop, contains: () => false}
});
let ROUTES = {};
const sandbox = {
  console, Math, Date, JSON, Map, Set, Number, String, Array, Object, Boolean, Promise, Error,
  isNaN, parseInt, parseFloat, performance: {now: () => 0},
  setTimeout: noop, setInterval: noop, clearInterval: noop, requestAnimationFrame: noop,
  fetch: async url => {
    const k = Object.keys(ROUTES).find(p => url.startsWith(p));
    const r = k === undefined ? {status: 404} : ROUTES[k];
    if (r.throw) throw new Error(r.throw);
    return {ok: r.status === undefined || r.status === 200, status: r.status || 200, json: async () => r.body};
  },
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  getComputedStyle: () => ({getPropertyValue: () => "#888"}),
  document: {
    getElementById: id => (els[id] ||= mk()), querySelector: () => mk(), querySelectorAll: () => [],
    createElement: () => mk(), createTextNode: t => ({data: String(t), textContent: String(t)}),
    addEventListener: noop, documentElement: mk(), body: mk()
  },
  window: {addEventListener: noop, devicePixelRatio: 1, location: {hostname: "10.81.81.1", host: "10.81.81.1"}},
  navigator: {userAgent: "oracle"},
  ResizeObserver: function(){ return {observe: noop, unobserve: noop, disconnect: noop}; },
  URL, URLSearchParams, TextEncoder,
  location: {href: "http://oracle/", origin: "http://oracle", pathname: "/"},
  alert: noop, prompt: () => null, confirm: () => false
};
sandbox.window.document = sandbox.document;
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
try { vm.runInContext(script, ctx, {timeout: 10000}); }
catch (e) { console.error("FATAL: page script threw at load: " + e.message); process.exit(1); }
for (const fn of ["buildFromSources", "tick", "clearAll", "renderNodeHeader", "renderPonds", "draw"])
  if (typeof ctx[fn] !== "function") { console.error("FATAL: " + fn + " not defined"); process.exit(1); }
const ev = expr => vm.runInContext(expr, ctx);
const state = () => ev(`({hosts:[...HOSTS.keys()].sort(), nodes:[...NODES.keys()].sort(),
  hub:HUB_LINKS.map(l=>l.from+">"+l.to+(l.label?" "+l.label:"")).sort(), db:DBHOST_IP, snap:SNAPSHOT!==null,
  links:[...PHYS_LINKS.keys()].sort(), sensors:SENSORS.length})`);

/* ---- the cases ---- */
const N1 = "10.81.81.1", N2 = "10.82.82.1", N3 = "10.83.83.1";
const regionSnap = {snapshot: 1, mode: "region", node: {name: "N1", dot1: N1}, build: {build: "fdf-2026-10-12-r20"},
  ponds: [{area: "test", forward: true, ram: "172.30.0.10:51888", config: null}], hub: [{iface: "wg0", handshake_age_s: 17}],
  members: [{ip: N1, name: "N1", role: "node", alive: true, self: true, source: "region", echo: `N1,${N1},192.168.1.1,0.0.0.0`},
            {ip: N2, name: "N2", role: "node", alive: true, source: "region", echo: `N2,${N2},${N1},0.0.0.0`},
            {ip: N3, name: "N3", role: "node", alive: false, source: "region", echo: `N3,${N3},0.0.0.0,0.0.0.0`}],
  elections: {databasehost: N2}, errors: []};
// sensors: N1 and N2 each hold a direct Topology route to the other, N2 one through N1 to N3 (transit), and each a
// route to an address outside the members (the old model's Internet edge)
const sensors = {rows: [
  {SensorID: 1, SensorType: "Topology", SensorName: "N1.Topology.Links",
   jsonData: JSON.stringify({links: [{peer_ip: N2, kind: "FAST", dev: "eth1", rtt_avg_ms: 3}, {peer_ip: "203.0.113.9", kind: "FAST", dev: "eth0"}]})},
  {SensorID: 2, SensorType: "Topology", SensorName: "N2.Topology.Links",
   jsonData: JSON.stringify({links: [{peer_ip: N1, kind: "FAST", dev: "eth1", rtt_avg_ms: 4}, {peer_ip: N3, kind: "FAST", dev: "eth1", via: N1}]})}]};
const lanSnap = {snapshot: 1, mode: "lan-only", node: {name: "G1", dot1: "10.61.61.1"}, build: {build: "b"}, ponds: [], hub: [],
  members: [{ip: "10.61.61.1", name: "G1", role: "node", alive: true, self: true, source: "lan", echo: "G1,10.61.61.1,172.30.0.1,0.0.0.0"},
            {ip: "10.62.62.1", name: "G2", role: "node", alive: true, source: "lan", echo: "G2,10.62.62.1,10.61.61.1,0.0.0.0"}],
  elections: {databasehost: "10.61.61.1"}, errors: ["/usr/local/bin/makeHostJson.bash: no output"]};
const clockRe = "\\d\\d:\\d\\d:\\d\\d";

(async () => {
  console.log("=== region: the map is the snapshot ===");
  ROUTES = {"/getSnapshot.php": {body: regionSnap}, "/api.php": {body: sensors}};
  await ctx.tick();
  let s = state();
  check(JSON.stringify(s.nodes) === JSON.stringify([N1, N2, N3, "pond:test"].sort()), "map nodes: the 3 members and the forward pond", JSON.stringify(s.nodes));
  check(JSON.stringify(s.hub) === JSON.stringify([`${N1}>pond:test wg0 17s`, `${N2}>pond:test`]), "hub links: each alive member to the pond, this node's labelled wg0 17s; none from the down N3", JSON.stringify(s.hub));
  check(s.db === N2, "databasehost from the snapshot's elections", s.db);
  check(!s.nodes.some(n => !n.startsWith("10.") && !n.startsWith("pond:")), "no Internet node, nothing the snapshot does not name");
  check(ev("typeof EDGES") === "undefined", "the page holds no edge model (EDGES is gone)");
  check(JSON.stringify(s.links) === JSON.stringify([[N1, N2].sort().join("|")]), "measured links (panel only): the one direct hop N1|N2; the transit route and the outside address are not links", JSON.stringify(s.links));
  const st = els.status.textContent;
  check(/^3 members · 2 hub links · 1 measured links · /.test(st) && !/uplink/i.test(st), "status: members, hub links, measured links; no uplinks", st);
  check(els.hdrNode.textContent === "N1 (10.81.81.1) · mode region · build fdf-2026-10-12-r20 · ponds *test · hub link wg0 17s", "header: name (.1), mode, build, ponds, hub link", els.hdrNode.textContent);
  check(els.lnkRegionWrap.hidden === false && els.lnkRegion === undefined, "region mode: the FD&F link is shown, and the script does not rewrite its href", "hidden=" + els.lnkRegionWrap.hidden);
  check(/href="\/fdf\/"/.test(html), "the FD&F link is /fdf/ on this server");
  check(els.pondTiles.textContent.includes("test  (forward)") && els.pondTiles.textContent.includes("2 members"), "ponds: the forward pond counts the snapshot's alive members", els.pondTiles.textContent);
  check(els.hostTiles.children.length === 3, "members panel: 3 tiles", els.hostTiles.children.length);

  console.log("\n=== getSnapshot.php fails: nothing stale, said what and when ===");
  ROUTES = {"/getSnapshot.php": {status: 500}, "/api.php": {body: sensors}};
  await ctx.tick();
  s = state();
  check(!s.snap && s.nodes.length === 0 && s.hosts.length === 0 && s.hub.length === 0 && s.db === "" && s.links.length === 0 && s.sensors === 0,
        "map, members, hub links, databasehost and sensor panels all cleared", JSON.stringify(s));
  const failRe = new RegExp(`^getSnapshot\\.php failed at ${clockRe}: HTTP 500 /getSnapshot\\.php$`);
  check(failRe.test(els.status.textContent), "status says what failed and when", els.status.textContent);
  check(failRe.test(els.hdrNode.textContent), "header says it too", els.hdrNode.textContent);
  check(failRe.test(els.pondTiles.textContent), "ponds panel says it too", els.pondTiles.textContent);
  check(els.lnkRegionWrap.hidden === true, "no region link without a snapshot");
  check(els.hostTiles.children.length === 0, "members panel empty");
  ROUTES = {"/getSnapshot.php": {throw: "connection refused"}};
  await ctx.tick();
  check(new RegExp(`^getSnapshot\\.php failed at ${clockRe}: connection refused$`).test(els.status.textContent), "a refused connection is said the same way", els.status.textContent);
  ROUTES = {"/getSnapshot.php": {body: {hello: 1}}};
  await ctx.tick();
  check(/^getSnapshot\.php failed at .*: its answer is not a snapshot/.test(els.status.textContent), "an answer that is not a snapshot is a failure", els.status.textContent);

  console.log("\n=== the next refresh tries again ===");
  ROUTES = {"/getSnapshot.php": {body: regionSnap}, "/api.php": {body: sensors}};
  await ctx.tick();
  s = state();
  check(s.snap && s.nodes.length === 4 && s.hub.length === 2, "drawn again from the new snapshot", JSON.stringify(s.nodes));
  check(ev("SNAP_FAIL") === null, "the failure is no longer shown");

  console.log("\n=== api.php fails: the map stands, the panels say so ===");
  ROUTES = {"/getSnapshot.php": {body: regionSnap}, "/api.php": {status: 502}};
  await ctx.tick();
  s = state();
  check(s.snap && s.nodes.length === 4 && s.hub.length === 2 && s.links.length === 0 && s.sensors === 0, "map from the snapshot, no sensor data kept", JSON.stringify(s));
  check(new RegExp(`· ! api\\.php \\(sensors\\) failed at ${clockRe}: HTTP 502`).test(els.status.textContent), "status says api.php failed and when", els.status.textContent);
  check(/api\.php \(sensors\) failed at/.test(els.engineTiles.textContent) && /api\.php \(sensors\) failed at/.test(els.linkTiles.textContent), "engine and links panels say it");

  console.log("\n=== LAN-only ===");
  ROUTES = {"/getSnapshot.php": {body: lanSnap}, "/api.php": {body: {rows: []}}};
  await ctx.tick();
  s = state();
  check(JSON.stringify(s.nodes) === JSON.stringify(["10.61.61.1", "10.62.62.1"]), "map nodes: the two members, no pond, no Internet", JSON.stringify(s.nodes));
  check(s.hub.length === 0 && s.db === "10.61.61.1", "no hub links; databasehost from the elections", JSON.stringify(s));
  const h2 = els.hdrNode.textContent;
  check(h2.startsWith("G1 (10.61.61.1) · mode lan-only · build b · ponds none, hub none (LAN-only)") && h2.includes("! /usr/local/bin/makeHostJson.bash: no output")
        && els.lnkRegionWrap.hidden === true, "LAN-only header, the snapshot's error said, no region link", h2);
  check(els.pondTiles.textContent.includes("LAN-only: this machine is in no pond."), "ponds panel: LAN-only", els.pondTiles.textContent);

  console.log("\n=== what the page no longer holds ===");
  for (const [re, what] of [[/EXTERNAL/, "EXTERNAL edges"], [/filterEgressTree|egress/i, "the egress tree"], [/uplink/i, "uplinks"],
                            [/parseEcho|gw0|gw1/, "echo upstream fields"], [/external:internet/, "an Internet node"], [/VIEW_MODE|btnView/, "a view toggle"],
                            [/:8090/, "a link to a separate php -S on :8090"]])
    check(!re.test(script.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "")), "no " + what + " in the page's code");
  check(!/broker/i.test(script), "no broker vocabulary in the page's script (comments included)");

  console.log();
  if (FAIL) { console.log(`FAILED ${FAIL} check(s)`); process.exit(1); }
  console.log("PASS \u2014 [MAP_FROM_THE_SNAPSHOT_V1] [ONE_MONITOR_TRUTH_V1]");
})().catch(e => { console.error("FATAL: " + (e && e.stack || e)); process.exit(1); });
