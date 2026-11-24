# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# sara_ui.py -- SARA's browser page (/ui) for the memory model.
#
# SARA's own /ui (deployments/sara_verified_local_v1/worldshepherd_sara/app.py) is a fixed page: it names the
# endpoints an operator may ask. On the memory there is something to see instead of something to ask: the read tickets
# as they stand, in every SARA's browser at once, because every SARA reads the same cells.
#
#     app.include_router(sara_ui.router(store))        # store: the SARA's RibbitClaimStore (ticket_sql: factory.cells)
#
#   GET /ui              the page: SARA's three cards as they were, and above them the memory -- where WorldShepherd's
#                        RAM is served now, the SARAs in it, and each ticket: who issued it, when it ends, how it stands
#   GET /ui/memory       what the page shows, as JSON, derived from one read of the service's cells.
#                        ?after=N&memory=M  is a held read: the SARA holds its read on the memory (after N) and
#                        answers when a cell is written. The browser asks again when it is answered. Nothing is asked
#                        on a timer, by the page or by the SARA.
#
# The page changes nothing: it has no write. It shows no ticket digest and no token. When WorldShepherd's RAM moves,
# the SARA is on the new memory (ribbit_claim_store.py), the held read ends, and the page shows the new one.
#
# To see it without SARA:   WS_RAM=HOST:PORT python3 -m uvicorn --factory sara_ui:standalone     (WS_RAM unset: the app's RAM on a node)
import os
import time

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

import ribbit
from ribbit_claim_store import APP, BUILD, SERVICE, RibbitClaimStore, Ticket

UI_BUILD = "sara-ui 0.8 [THE_PAGE_IS_A_HELD_READ_V1]"


def as_it_stands(store, after=-1, memory_id=""):
    """the memory as the page shows it. after >= 0 and the same memory: held until a cell is written (or the hold ends)."""
    def read(memory):
        same = memory_id == "%s#%d" % (memory.where, id(memory))
        cells = memory.read(SERVICE)
        top = max([c["id"] for c in cells] + [0])
        if same and after >= top:                              # nothing written since the page last read: hold
            if memory.read(SERVICE, "", "", after=after, wait_s=ribbit.HOLD_S):
                cells = memory.read(SERVICE)
                top = max([c["id"] for c in cells] + [0])
        return memory, cells, top
    memory, cells, top = store._in_the_memory(read)
    now = time.time()
    families = {}
    for c in cells:
        if c["variable"].startswith("ticket."):
            families.setdefault(c["variable"][7:], []).append(c)
    tickets = []
    for ticket_id, family in families.items():
        t = Ticket(family)
        if t.issue is None:
            tickets.append({"id": ticket_id, "stands": "not registered", "issuer": "", "ends_in": None, "by": "", "claims": len(t.ids)})
            continue
        exp = float(t.issue["bag"]["exp"])
        stands = "consumed" if t.winner else ("expired" if now > exp else "issued")
        tickets.append({"id": ticket_id, "stands": stands, "issuer": t.issue["instance"].split(".")[1],
                        "ends_in": round(exp - now), "by": t.winner["instance"].split(".")[1] if t.winner else "",
                        "claims": sum(1 for i in t.ids if i.startswith("claim."))})
    tickets.sort(key=lambda t: (t["stands"] != "issued", t["ends_in"] is None, -(t["ends_in"] or 0)))
    return {"build": BUILD, "ui": UI_BUILD, "me": store.who, "app": APP, "at": memory.where.split("/")[0],
            "memory": "%s#%d" % (memory.where, id(memory)), "top": top,
            "saras": sorted(c["instance"] for c in cells if c["variable"] == "sara" and c["bag"]),
            "counts": {s: sum(1 for t in tickets if t["stands"] == s) for s in ("issued", "consumed", "expired")},
            "tickets": tickets[:500]}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Worldshepherd SARA</title><style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:3rem auto;padding:0 1rem;background:#0b1020;color:#e8edf7}
.card{border:1px solid #34415f;border-radius:12px;padding:1rem;margin:1rem 0;background:#121a2e}
code{color:#9ad5ff} .ok{color:#96e6a1} .warn{color:#ffd28a} .dim{color:#8a97b5}
table{width:100%;border-collapse:collapse;font-size:.92rem} th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #26324d}
th{color:#8a97b5;font-weight:600} .n{font-size:1.5rem;font-weight:700} .row{display:flex;gap:2rem;flex-wrap:wrap}
.pill{display:inline-block;border:1px solid #34415f;border-radius:999px;padding:.1rem .6rem;margin:.15rem .3rem .15rem 0}
.me{border-color:#96e6a1;color:#96e6a1} .scroll{overflow-x:auto}
</style></head><body><h1>Worldshepherd SARA</h1>
<p id="state" class="warn">Reading the memory.</p>
<div class="card"><strong>The memory</strong>
<p>WorldShepherd's RAM is served at <code id="at">-</code>. This SARA is <code id="me">-</code>.</p>
<div id="saras"></div></div>
<div class="card"><strong>Read tickets</strong>
<div class="row"><div><div class="n" id="n-issued">-</div><div class="dim">issued, not consumed</div></div>
<div><div class="n" id="n-consumed">-</div><div class="dim">consumed</div></div>
<div><div class="n" id="n-expired">-</div><div class="dim">expired unclaimed</div></div></div>
<div class="scroll"><table><thead><tr><th>Ticket</th><th>Stands</th><th>Issued by</th><th>Consumed by</th><th>Ends in</th><th>Claims</th></tr></thead>
<tbody id="tickets"></tbody></table></div>
<p class="dim">A ticket is its cells in the memory. Every SARA derives how it stands from the same cells; this page is one held read of them.</p></div>
<div class="card"><strong>Authority separation</strong><p>CRE1AWS approves high-impact releases. SSPADAWANZZ operates the local service.</p></div>
<div class="card"><strong>Security boundary</strong><p>Tokens are never stored in this page. Ticket digests are not shown. This page writes nothing. PRIME SENTINEL private signing keys are not stored by SARA.</p></div>
<p class="dim" id="build"></p>
<script>
const $ = id => document.getElementById(id);
const text = (tag, s, cls) => { const e = document.createElement(tag); e.textContent = s; if (cls) e.className = cls; return e; };
function show(m) {
  $("at").textContent = m.at; $("me").textContent = m.me; $("build").textContent = m.build + "; " + m.ui;
  $("saras").replaceChildren(...m.saras.map(s => text("span", s, "pill" + (s === m.me ? " me" : ""))));
  for (const k of ["issued", "consumed", "expired"]) $("n-" + k).textContent = m.counts[k];
  $("tickets").replaceChildren(...m.tickets.map(t => {
    const r = document.createElement("tr");
    r.append(text("td", t.id.length > 18 ? t.id.slice(0, 18) + "…" : t.id), text("td", t.stands, t.stands === "issued" ? "ok" : t.stands === "consumed" ? "" : "warn"),
             text("td", t.issuer), text("td", t.by), text("td", t.ends_in === null ? "" : t.ends_in > 0 ? t.ends_in + " s" : "ended"), text("td", String(t.claims)));
    return r; }));
  $("state").textContent = "On the memory at " + m.at + ": " + m.saras.length + " SARA(s), " + m.tickets.length + " ticket(s)."; $("state").className = "ok";
}
async function read(after, memory) {            // each request is a read the SARA holds on the memory; answered, it is asked again
  try {
    const r = await fetch("ui/memory?after=" + after + "&memory=" + encodeURIComponent(memory));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const m = await r.json(); show(m); read(m.top, m.memory);
  } catch (e) {                                 // this SARA did not answer: said. Nothing is asked again on a timer
    $("state").textContent = "This SARA did not answer (" + e.message + "). Reload the page when it is up."; $("state").className = "warn";
  }
}
read(-1, "");
</script></body></html>"""


def router(store):
    r = APIRouter()

    @r.get("/ui", response_class=HTMLResponse)
    def ui() -> str:
        return PAGE

    @r.get("/ui/memory")
    def memory(after: int = -1, memory: str = "") -> dict:
        return as_it_stands(store, after, memory)

    return r


def standalone():
    """a page with no SARA behind it, on its own session:  python3 -m uvicorn --factory sara_ui:standalone"""
    a = FastAPI(title="Worldshepherd SARA: the memory")
    a.include_router(router(RibbitClaimStore(os.environ.get("WS_RAM") or None, name="ui")))
    return a
