# T5 brief — one monitor truth, web pages that describe what the system does

Tree /home/claude/clean (git; T0..T4c committed; read docs/cleanup-2026-10/T4c.md "Found, not done" and T4b.md). Edit
in place, do NOT commit. Rules: no fallbacks; code is the truth; nothing new in /usr/local/bin.

Facts to start from (verify each before relying on it):
- frognet_monitor (C++: usr/local/bin/frognet_monitor_cpp; Python: usr/local/bin/frognet_monitor_py; C#:
  frognet_monitor_cs) reads getHosts.php / databasehost / local probes. In region mode getHosts.php serves
  region_hosts.json the daemon writes (devices included: [DEVICE_ROW_V1]).
- opt/fdf/bin/fdf-monitor reads the region directly (participants, sessions, apps, terminals);
  opt/fdf/www/fdf/{fdf_dashboard.html,getParticipants.php,waitParticipants.php} is the FD&F web view.
- var/www/html/fln_dashboard32.html draws a "Broker uplink" edge model and databasehost-centred rendering.
- var/www/html PHP endpoints: getHosts.php, getPonds.php, frognet_echo.php and others; some have no caller.
- Old site copies, setup pages: setup_frognet.html / _dark.html (broker card removed in T3b).

## Do
1. Monitor data contract: one snapshot per node with: build tag, mode (LAN-only|region), ponds this node is in (forward
   marked), members (name, guid, .1, role node/leaf/participant, alive, from which source), hub link state (wg handshake
   age), elections (databasehost), and for each row where it came from (region record vs LAN discovery). Serve it from
   ONE endpoint on the node (extend getHosts.php or add getSnapshot.php — pick the one that keeps existing callers
   working; say which). Region mode: built from the daemon's region_hosts.json / region view files; LAN-only: from
   discovery's output. No second source per field.
2. frognet_monitor_py and the C++ monitor read that endpoint; show mode, ponds, hub link and build; remove broker
   vocabulary from what they display. The C# monitor: same change if it builds here (`dotnet` may not exist; if not,
   change it to match and say it is unbuilt). Keep frognet_monitor_shared/parser_vectors.json and its three parser
   oracles passing (update vectors for any new field, add vectors for mode/ponds/hub).
3. fln_dashboard32.html: replace the broker uplink model with ponds and hub links (from the same endpoint); no broker
   words in the live UI. fdf_dashboard (region view): keep; make both pages link to each other and use the same names
   for the same things.
4. PHP endpoints: list every var/www/html and opt/fdf/www PHP file with its callers (pages, scripts, monitors, apps:
   check frognet-android and the Windows bridge too). Remove the ones nothing calls; keep the rest; the T4a node file
   set must agree.
5. index.html / index.php (the MAMP default page, "Admin BlackBox Portal foo"): a plain node landing page that links
   the setup page, the dashboard, the monitor endpoint and says the node's name, mode and build.
6. Apache vhost: 000-default.conf proxies /semantic/ to 9100 (frognet-dashboard unit, removed in T1): remove the
   dead proxy; devices-site.conf DocumentRoot /var/www/www_devices/public_html is missing: remove the site or the
   line; admin-site.conf ServerName FrogNetAdmin.seattle5 and devices-site FrogNetDevices.SilverBox are John's names:
   make them the node's own. www_admin (writes Commands/netCommand.*): check callers; legacy → remove with its vhost.
## Gates
- parser oracles (py/cpp/cs where buildable), the monitor's own tests (frognet_monitor_py tests, cpp tests), the
  T4 container gate G1 (LAN-only) and G3 (region) showing the snapshot endpoint and the monitor output (capture the
  monitor's non-interactive / one-shot mode if it has one; if not, add a --once flag that prints one frame),
  selfcheck, discovery oracles, netns region-loop + LAN-only, static-no-broker (extend it to var/www/html pages and
  the monitors), static-no-fallback.
Write docs/cleanup-2026-10/T5.md. Final message: 5 lines.
