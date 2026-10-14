# Open for John — 2026-10-11

The book now says what the code does in every case below. Each item is a question about the CODE (or a ruling),
not about the text. File:line refers to the 10-11 tree (repo e889208 + the 10-11 overlay).

## Rulings
1. SAME identifier: the code computes HMAC-SHA256 keyed by /etc/frognet/sem_cache_secret (core/semcache_id.py:43-61,
   ribbit/cpp fnwp_server.hpp:106-111). Your 07-30 ruling: "It's not a MAC either ... It is the fnid." Which changes?
2. Part XII Oracle 1: with the broker routing the pond's 10/8, root on the broker can open an exchange with a node's
   :80 or :9009, so its red condition is met as written. Restate or keep?
3. The legal-surface FOR THE GUILD box sits in Part XIII (Simulator). Move it to Part XII?
4. Appendix L (doctrine index) prints "not generated": doctrineScan() reads only the top level of
   ribbit/examples/communicator, *.py/*.sh — the Python Communicator that is gone. Scan the whole tree (811 tags in
   .py/.sh today), or retire the appendix?
5. Appendix K: the per-node allocator is written "designed and not yet built". Change if work is under way.

## Fallbacks in the code (against "no fallbacks")
6. usr/local/bin/frognet_alive.bash:20-28,56-60 retries on .1 when .2 does not answer.
7. Liveness probe _get_alive_return_ip falls back to the /etc/hosts identity, then 0.0.0.0.
8. propogateNotificationInternal falls back to every 10.x.y.1 in /etc/hosts when discovery.neighbors fails.
9. frognet-tunnel-setup-v3.sh:179 takes NETWORK_NAME from gateways.conf when the pond name is missing.
10. frognet_monitor_py/discovery.py:96-108 falls back from getHosts.php to /etc/hosts.
11. simulation/netns_backend.py:194 [WG_USERSPACE_FALLBACK_V1] uses wireguard-go when wireguard.ko is absent.

## Dead or missing code
12. proxy/decision.py:332 calls _update_rtt_state(dev="") — no RTT sample is ever taken; the semantic/plain switch
    never switches. Fix the call or delete the switching code.
13. frognet-mediahost.service:33 runs ribbit/examples/communicator/fnav.py (not in the tree); install_mediahost.sh
    needs a missing frognet_communicator.py. Retire the mediahost role or point it at something that exists.
14. frognet-reaper.service:34 runs frognet_reaper.py (not in the tree); core/frognet_tuples.py:341-345 calls it
    "the guarantee". Restore or drop.
15. proxy_metrics.py:1518,1530 / daemon_metrics.py:768,779 call _T.on_database_change() and _T.heartbeat(), which
    core/frognet_tuples.py does not define. Wire or remove.
16. frognet-pond still has `ping` and `result` (broker v4 serves neither); frognet-broker-admin.sh create-passcode
    POSTs /api/v1/passcodes (v4 does not serve it). Remove?
17. No disaster-area oracle in the tree (Appendix E). Restore to discovery/?
18. FrogSim (components.py, reported_run.py, report.py, tuplestore.py, appspace.py, negative controls) is not in
    the published tree. Ship it?
19. The loose broker copy opt/frognet_semantic/broker/opt/frognet_broker_v4 is older than broker/full_broker.tgz.
    The book follows the bundle. Remove or refresh the loose copy?

## Defects
20. Broker FORWARD rule broker_namespace_v4.py:128-131 allows UDP 51820-51900; tunnels get 51901-51999 by default,
    51000-59999 with the shipped drop-in. Covers neither.
21. frognet-ssid-projection:55-66 rewrites ssid_projection.conf with SSID_PROJECTION only, dropping AP_IFACE and
    WIFI_COUNTRY (the node reverts to wlan0/US).
22. discovery/sim/system.py:510 max_hops=12 gives 299/357 false unreachable pairs on the large shapes; 64 clears them.
    System.remove_node (system.py:356-360) leaves a removed AP's stations attached.
23. Duplicate (pond, name) registration returns HTTP 500 (uncaught IntegrityError), not a 409 naming the conflict.
24. A pond created without a password admits any node that knows its name; /api/v4/bootstrap is unauthenticated and
    hands the admin token to the first caller. (Your stance: security is the operator's; listed so it is a choice.)
25. The installer's listen drop-in (install_frognet_broker.sh:462) omits --no-server-header.
26. No shipped unit sets LimitNOFILE (your policy: infinity).
27. semantic_monitor.sh has the never-reads-stdin bug the old metric_upsert.sh example had.
28. frognet_monitor_py: fetch_sensor_detail on the main loop (main_loop.py:425, up to 6 s freeze); identity.py:146-148
    print()s onto the curses screen.
29. Stale comments: frognet_monitor_cpp/src/dashboard.cpp:72 ("same order as the Python"), rinab/src/rina_region.cpp:1008
    (SRV record), discovery/live.py:500 (probes unchanged in region mode).
30. The 50% WINNER_HYSTERESIS still decides when the incumbency hold does not engage. Keep?

## Not done
- Appendix J step 6's ladder walk is the earlier Python Communicator's recording, labelled as such. A C++ walk needs
  a recorded run.
- The published site's copy (broker/var/www/html: build_magnum.js, Magnum Croakus.html, -print.html, .docx) is the
  old book. This generator replaces it when you publish.
