# Testing the Family Calendar web UI in a browser

The web UI (`web/index.html`) is an ordinary HTTP client. It talks to the codex at
`/unrest/family-calendar/<verb>` on whatever host serves it. There are two ways to
exercise it.

## A. On the box (real)
1. Install the bundle (`install_family_calendar.sh`) so the codex answers the suffix
   behind the local Apache and the page is deployed to the docroot.
2. From a device on the mesh, open:
   `http://family-calendar.frognet/family-calendar/`
   (well-known name → the page; the page calls the codex suffix on the same origin)
3. You should see the agenda, a presence line, and an "add event" form. Add an event
   on one device; within one poll interval (~4s) it appears on every other device —
   that's the one live shared element.
4. **Fail-clean check:** stop the codex (or pull the device off the mesh). The status
   pill should switch to "can't reach calendar — contact admin" rather than showing
   stale data.
5. **Split check:** if a re-elected/stranded host serves an older `ts`, the UI shows
   "disconnected (split detected)" and stops polling instead of forking.

## B. Locally in a browser (no box, no edits) — recommended

One server hosts both the page and the codex on the same origin, so `index.html`
works **unmodified** (its relative `/unrest/family-calendar` path resolves to this
server):

```
python3 dev/serve_web_test.py            # prints a URL; serves UI + codex on :8888
```

Then open **http://127.0.0.1:8888/** in your browser. You'll see the agenda, the
add-event form, and the presence line. To test:
- Add an event → it appears in the list within a poll (~4s).
- Open a **second browser tab** to the same URL → both tabs reflect the same shared
  element (add in one, it shows in the other). That's the one live element.
- Delete by tapping the ✕ on a row.
- **Fail-clean:** stop the server (Ctrl-C) → the status pill flips to
  "can't reach calendar — contact admin"; no stale data.

(Override the port: `python3 dev/serve_web_test.py 9000`.)

## C. Point the page at a separate mock (advanced)
If you'd rather run the standalone mock (`dev/mock_codex_server.py`, :8800) and open
`index.html` from the filesystem, change the one `SUFFIX` line in `web/index.html` to
`"http://127.0.0.1:8800/unrest/family-calendar"` — and revert it before shipping, since
on the box the page and codex are same-origin.
