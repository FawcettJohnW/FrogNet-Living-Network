#!/usr/bin/env python3
"""tools/dc_demo_e2e.py BASE_URL [SHOT_DIR] -- [DC_APPS_V1] the first DisasterComm demonstration, driven through the real pages
in a headless browser, one browser context per person (each its own device):
  1 a person registers a help request (medical, 2 people)          help.html
  2 two units publish themselves (Medic 7 near, Engine 12 farther)  responders.html
  3 two hospitals publish (Harborview open, Valley on diversion)    hospitals.html
  4 the coordinator picks the request, sees Medic 7 nearest and Harborview receiving, assigns   coord.html
  5 the person's page shows the assignment WITHOUT a reload (held read), and the chat room has the line   chat.html
Each step says PASS or FAIL; the result is the last line. BASE_URL is where www/ is served (e.g. http://127.0.0.1:21700)."""
import sys, time
from playwright.sync_api import sync_playwright

base = sys.argv[1].rstrip('/'); shots = sys.argv[2] if len(sys.argv) > 2 else None
fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok: fails.append(what)

with sync_playwright() as p:
    b = p.chromium.launch()
    def page(): return b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    stamp = str(int(time.time()))[-5:]
    person = page(); person.goto(f"{base}/dc/help.html")
    person.fill("[name=name]", f"Maria Lopez {stamp}"); person.fill("[name=people]", "2")
    person.check("[name=need][value=medical]"); person.fill("[name=place]", "412 Pine St, apt 3")
    person.fill("[name=lat]", "47.6150"); person.fill("[name=lon]", "-122.3290"); person.fill("[name=contact]", "206-555-0142")
    person.click("button:has-text('Publish')"); person.wait_for_selector("text=Responders can see it now", timeout=10000)
    check(True, "1 help request published")

    for name, caps, lat, lon in [(f"Medic 7 {stamp}", ["ALS", "transport"], "47.6100", "-122.3300"), (f"Engine 12 {stamp}", ["fire", "rescue"], "47.6500", "-122.3500")]:
        u = page(); u.goto(f"{base}/dc/responders.html"); u.fill("[name=unit]", name)
        for c in caps: u.check(f"[name=cap][value={c}]")
        u.fill("[name=lat]", lat); u.fill("[name=lon]", lon); u.click("button:has-text('Publish')"); u.wait_for_selector("text=Coordinators can see", timeout=10000)
    check(True, "2 two units published")

    for name, rec, caps, er, lat, lon in [(f"Harborview {stamp}", "open", ["trauma", "burn"], "4", "47.6040", "-122.3240"), (f"Valley {stamp}", "diversion", ["trauma"], "0", "47.4400", "-122.2100")]:
        h = page(); h.goto(f"{base}/dc/hospitals.html"); h.fill("[name=name]", name); h.select_option("[name=receiving]", rec)
        for c in caps: h.check(f"[name=cap][value={c}]")
        h.fill("[name=er]", er); h.fill("[name=lat]", lat); h.fill("[name=lon]", lon); h.click("button:has-text('Publish')"); h.wait_for_selector("text=EMS can see", timeout=10000)
    check(True, "3 two hospitals published")

    co = page(); co.goto(f"{base}/dc/coord.html"); co.fill("#me", "dispatch-1")
    co.click(f"#reqs .item:has-text('Maria Lopez {stamp}')")
    co.wait_for_selector(f"#units .item:has-text('Medic 7 {stamp}')", timeout=10000)
    first = co.inner_text("#units .item >> nth=0")
    check(f"Medic 7 {stamp}" in first, "4a nearest available ALS unit for the medical request is Medic 7")
    hos = co.inner_text("#hosps")
    check(f"Harborview {stamp}" in hos and f"Valley {stamp}" not in hos, "4b Harborview (open) offered, Valley (diversion) not")
    co.click(f"#units .item:has-text('Medic 7 {stamp}')"); co.click(f"#hosps .item:has-text('Harborview {stamp}')")
    co.fill("#note", "two patients, one with chest pain")
    if shots: co.screenshot(path=f"{shots}/coord-before-assign.png", full_page=True)
    co.click("#assign"); co.wait_for_function("document.getElementById('say').textContent.length > 0", timeout=10000)
    check("posted to the chat" in co.inner_text("#say"), "4c assigned and posted to the chat")

    t0 = time.time()
    person.wait_for_selector(f"text=assigned >> xpath=.. >> text=Medic 7 {stamp}", timeout=30000)
    check(True, f"5a the person's page shows the assignment without a reload ({time.time() - t0:.1f} s)")
    if shots: person.screenshot(path=f"{shots}/help-after-assign.png", full_page=True)
    ch = page(); ch.goto(f"{base}/fdf/chat.html")
    ch.wait_for_selector(f"text=ASSIGNED Medic 7 {stamp}", timeout=30000)
    check(True, "5b the chat room carries the assignment")
    if shots: ch.screenshot(path=f"{shots}/chat.png", full_page=True)
    b.close()
print("RESULT " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
sys.exit(1 if fails else 0)
