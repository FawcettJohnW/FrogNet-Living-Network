# T5 working notes

## Baseline (tree 546df37, before any T5 edit)
- py parser oracle: FATAL, identity.parse_echo_line / dot_one_of missing (red at T4c; nobody ran it).
- py merge oracle: ALL PASS.
- C++ monitor: builds here once libcurl4-openssl-dev is on this host (sandbox tool, not the tree); ctest 2/2.
- C# monitor: dotnet-sdk-8.0 installed on this host; builds; selftest 48/48.
- Broker words in the monitors and var/www/html pages: one, parser_vectors.json ("brokerhost.seattle2", test data).
  fln_dashboard32.html's "Broker uplink" wording went in T3b; the model (echo gateways -> EXTERNAL "uplink" edges
  child -> parent -> Internet) is still there.

## Found while reading
- getDbHost.php answers from the resolver (gethostbyname): the monitor's own comment says the resolver and /etc/hosts
  disagree in practice; the dashboard used it AND a Topology-sensor LOCAL alias for the databasehost (two sources).
- handshake_rtts.json is written with mkstemp (0600): www-data cannot read it.
- Apache listens on 8080/8443 only (installer ports.conf); admin-site.conf (*:80), databasehost.conf (*:80),
  databasehost-ssl.conf / frognet-ssl's second vhost (*:443) and devices-site.conf (*:80) can never be reached.
- Commands/netCommand.* (registerHost.php, registerAndForward.php, frognet_add_network.php, runMerge.php): nothing in
  the tree reads that directory; PropogateHost's only callers are those command files; propogateMergeGuid.bash has none.
- frognet-dashboard.service is still in etc/systemd/system (said removed 2026-08-29): /opt/frognet/venv and
  /opt/frognet/dashboard exist nowhere; 000-default.conf proxies /semantic/ to its port 9100.
- installer E4 sed: "ServerName NODENAME_PLACEHOLDER" does not match the file's "FrogNetAdmin.NODENAME_PLACEHOLDER";
  the first sed (FrogNetAdmin\..*) does.

## Done so far (in order)
1. var/www/html/frognet_node.inc.php (new): one function per field; getSnapshot.php (new) serves fn_snapshot();
   getHosts.php and frognet_echo.php call fn_host_rows / fn_self_echo (same output as before). php -l OK; region and
   LAN fixtures (scratch roots via FROGNET_SNAPSHOT_ROOT) give the expected objects.
2. graph_apply._write_hosts_json: + guid per row; graph_apply_oracle + 1 check (ALL OK).
3. etc/sudoers.d/frognet-snapshot (new tree file, world manifest): www-data may run exactly
   `/usr/bin/wg show all latest-handshakes` (visudo -cf OK). Installer D5 chmod 0440 + visudo.
4. Apache: 000-default /semantic/ proxy gone; admin-site.conf, devices-site.conf, var/www/www_admin,
   frognet-dashboard.service deleted (installer B3/D5/E4, reset, world manifest, make_build_tar, boot_gate_check).
5. PHP: 28 web files with no caller deleted (+ PropogateHost, propogateMergeGuid.bash, var/www/html/frognet_monitor);
   frognet_prune_removed.sh t5_section names them.
6. Python monitor: discovery.parse_snapshot/fetch_snapshot/hosts_of; identity.parse_echo_line/dot_one_of (the py
   parser oracle was red: FATAL import); title/mode/build/ponds/hub lines; elections from the snapshot (the /etc/hosts
   reader in identity.py gone); --once (once.py). parser vectors: + snapshot (8 cases), brokerhost -> otherhost.
   py parser oracle 35/35, test_snapshot_view_oracle 14 ok, merge oracle ALL PASS, frognet_sim ALL TESTS PASS
   (its identity test now checks dot_one_of: parse_local_ip_from_ip_output and its 127.0.0.1 stand-in are gone).
7. C++ monitor: parseSnapshot/NodeSnapshot, Client::snapshot/hostsOf (discoverHosts and the /etc/hosts databasehost
   read gone), titleText/nodeLines in both dashboards, `frognet_monitor snapshot` (one frame). Built here (libcurl dev
   headers installed on this host): 0 warnings; parser oracle 37/37, dashboard oracle 32/32.
8. C# monitor: Parsers.ParseSnapshot/TitleText/NodeLines, Client.SnapshotAsync/HostsOf (DiscoverHostsAsync,
   HostsFilePath, the /etc/hosts read gone), card header, `frognet-monitor snapshot`, demo with a sample snapshot.
   dotnet-sdk-8.0 installed on this host: builds with TreatWarningsAsErrors; selftest 60/60.
9. fln_dashboard32.html: one read (getSnapshot.php): members, databasehost (elections), ponds panel, header node line,
   pond node + hub links (member -> forward pond, this node's labelled with the handshake age); getDbHost.php, the
   Topology LOCAL databasehost guess, jgetTry and the "Transient empty" keep-old-picture gone; Networks -> Ponds,
   Hosts -> Members; links: node page, getSnapshot.php, setup, FD&F region members (:8090, region mode).
   oracle_dashboard32_views.js + 6 checks: PASS.
10. fdf_dashboard.html (3 copies, cmp-identical): "FD&F region members", Member column, link to the node dashboard.
11. index.php = the node landing page (name, .1, mode, build, links); index.html and MAMP-PRO-Logo.png deleted.
12. netns-reach-test (2a) reads getSnapshot.php instead of getPonds.php; make_deliverables FROGNET_FILES + the new
    files; header.sh: sudoers 0440 + visudo (refused -> removed, exit 1). dist rebuilt 00:37Z.
13. static-no-broker: + strict pass (pages + monitors, nothing excluded): 0 violations. static-no-fallback: +7 T5
    patterns: 0 violations, 43 allowed. discovery oracles 84/84; selfcheck 14/14. Build ids: installer
    frognet-install-2026-10-12-t5, tunnel daemon frognet-tunnel-2026-10-12-t5 (FD&F tag r20 unchanged).
14. Installer F6: + "snapshot:" check (getSnapshot.php answers snapshot 1 with a mode).
15. (unlogged before the interruption, 17:47) 000-default.conf: DirectoryIndex index.php (Debian's own index.html stays,
    unserved); frognet_prune_removed.sh t5_section removes var/www/html/index.html only when it is FrogNet's old
    "Admin BlackBox Portal foo" page.

## Resumed after the docker restart (old g1 d2 n1 n2 removed, all fresh)
16. .run checked: its 48 FrogNet files cmp-identical to the tree before any resumed edit (000-default.conf and
    frognet_prune_removed.sh, newer than the .run, are not in it).
17. G1 found: frognet_node.inc.php header named /etc/sudoers.d/frognet-setup; the file is frognet-snapshot (comment).
18. G3 found: region snapshot pond area "" + error "/opt/fdf/var/site.env names no RINA_AREA": site.env is 0600
    root (it holds the site key), www-data cannot read it. fn_ponds now takes the area from facts.json `pond` (the
    daemon's RINA_AREA, region.py writes it). Copied onto n1/n2/g1 by hand: area test, errors [].
19. dist rebuilt 04:14Z (make_deliverables-full.txt) so the .run carries the fixed frognet_node.inc.php: 48 FrogNet
    files cmp-identical; applied to n2 as an update (fdf-install-x86_64.run KEY RAM AREA, EXIT 0): its
    frognet_node.inc.php md5 = the tree's, snapshot area test, errors [].
20. Gates all re-run on the final tree; T5.md written.
