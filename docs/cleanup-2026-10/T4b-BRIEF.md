# T4b brief — what stands between this tree and other people's machines, after T4a

Tree /home/claude/clean (git; T0..T4a committed). Read docs/cleanup-2026-10/T4a.md first (the container harness is in
t4a-tools/; reuse it). Edit in place, do NOT commit. John's rules: NO FALLBACKS ("a failed step is logged and retried on
the next event, never replaced by a second mechanism; gone is gone"); builds logged by the software itself; nothing
stray in /usr/local/bin; code is the truth.

## Do
1. Fallbacks on the runtime/install path — remove each (one mechanism; failure logged, retried on the next event).
   Verify each still exists first (file:line), read it, change it, and note what the single mechanism is now:
   - usr/local/bin/frognet_alive.bash: retries on .1 when .2 does not answer (discovery is on .2, never rescued by .1).
   - the liveness probe's return address (_get_alive_return_ip) falling back to the /etc/hosts identity then 0.0.0.0.
   - propogateNotificationInternal falling back to every 10.x.y.1 in /etc/hosts when discovery.neighbors fails.
   - frognet-tunnel-setup-v3.sh is gone (T3b); check no other gateways.conf NETWORK_NAME pond fallback remains.
   - frognet_monitor_py/discovery.py:96-108 getHosts.php -> /etc/hosts fallback.
   - discovery/resolv.py always appending 8.8.8.8 to resolv.conf.
   - setup_lillypad_v4.bash: "try multiple activation methods" / disconnect-reconnect / "forcing IP assignment" chain
     (416-445); `mariadb || mysql` (241, 716) and the same in frognet_reset.sh; `getEth0Address || echo $GATEWAY_IP`
     (792); frognet_eth0_fixup.sh forcing IPs and then also NetworkManager (673-679) — one owner of the address.
   - installer: B1 writing procfs directly beside sysctl ("belt-and-suspenders" 494-497); fix_unbound_bypass.sh ||
     stop unbound (1181); the netstart AP-interface belt-and-suspenders (1911); C3 falling back from venv python to
     /usr/bin/python3 (855) — see 3.
   - install_databasehost.sh chain of package names (75-81).
   - fdf-facts sh() returning "" on any error (35-39): failures must be logged, not swallowed.
   - fdf-facts pinging 8.8.8.8 to judge the uplink (30): judge it from the uplink's own state or a configured target;
     no hard-coded public address. fdf-facts 1 s mtime poll of the lease file: an event (inotify) if available in the
     tree's Python, else say why it stays.
   - the @reboot `sleep 240` merge cron and any other timer the installer adds that polls (the merges are event-driven
     by the NetworkManager hook): remove if the hook covers boot; if boot needs one merge, one systemd unit
     After=network-online.
   - runMerge.bash FROGNET_MERGE_LEGACY_TAIL (if T3b left it).
   For anything you find that is NOT a fallback but platform support, see 2.
2. WireGuard implementation is chosen once at install, by platform, and recorded (e.g. /etc/frognet/wg.impl =
   kernel|wireguard-go, written by the installer and by fdf-region-create from `modprobe -n wireguard` / the module's
   presence). fdf-hub (kernel then wireguard-go today, fdf-hub:74-78), the tunnel daemon, and the simulation's
   netns_backend [WG_USERSPACE_FALLBACK_V1] read the recorded choice and use only it; a failure is logged and retried.
   The netns test harness sets its choice explicitly (it uses userspace today via FDF_WG_USERSPACE=1).
3. Services run on the venv the installer builds (T4a: the system pip leaves 8 helper scripts in /usr/local/bin, and
   Ubuntu refuses system pip for some packages): frognet-proxy, frognet-daemon, frognet-tunnel-daemon-v3 and any other
   python unit use /opt/frognet_semantic/venv/bin/python3; A3's system-wide pip goes; C3 uses the venv only. Check the
   81 shebangs that already point at the venv and the ones that point at /usr/bin/python3 for units/scripts.
4. Uplink by function, not by name: T4a G2 converged only after the link into another node's LAN was renamed wlan0
   (discovery reads mapInterfaces' wlan0Name as the upstream). On a real machine that link can be eth1/enp2s0/usb0.
   Find where the upstream role is decided (mapInterfaces, identity, discovery.walk), and decide it from what the link
   is (the interface whose DHCP lease came from another FrogNet .1, or whose default route is) — one rule. Keep the
   oracles that pin names (oracles/oracle_mapinterfaces_names.sh etc.) passing or update them to the rule. Re-run G2
   WITHOUT the rename.
5. Builds: the FrogNet base gets a release id. One tag for the whole tree for this cleanup: bump FDF_BUILD_TAG and the
   BUILD_TAGs make_deliverables.sh checks (rinab/src/fdf_version.hpp, fdf-hub, fdf-facts, fdf_graph.py, region.py) to
   `fdf-2026-10-12-r20` (keep the format), and give the FrogNet base /opt/frognet_semantic/BUILD with the same tag and
   the git commit when built from git. The installer prints it, the proxy/daemon/tunnel daemon log it at start (the
   proxy's fixed BUILD=2026-03-24-v1 goes). Rebuild the binaries that embed the tag if the build scripts run here
   (rinab tools/build.sh, build_static_host.sh, build_arm64.sh; the build/ dir is a symlink — build into the tree's own
   build dir instead if needed, and say how long it took), then rebuild opt/fdf/dist with make_deliverables.sh.
6. Licence headers: discovery/test_license_oracle names the files missing the GPL-2.0-only header (39 at T4a). Add the
   tree's standard header (copy it exactly from an existing file of the same language) to each. That includes the C++
   Communicator files: it is John's code, same licence.
7. Hard-coded deployment specifics left after T4a: opt/fdf/fdf.env RINA_AREA=rina-area.test vs frognet elsewhere (one
   default or none); opt/fdf/windows/bridge/etc/ponds.txt and frognet-android/windows/etc/ponds.txt and the Android
   Identity.kt default pond (165.232.153.144): make the Windows ponds.txt an example the operator fills in (the bridge
   refuses with a message when it is still the example); for Android, leave the code (needs an app rebuild) and list it.
   Any other 165.232.153.144 / StreamingFrog / Seattle default in shipped runtime (not docs, not tests): remove.
8. frognet_system_tune.sh vs frognet-system-tune.sh: one file (the unit's name), the installer calls that.

## Gates
  The T4a container gates G1..G5 again on the result (G2 without the wlan0 rename), the T3b gates (selfcheck, discovery
  oracles — test_license should now PASS — run_all, the netns suite; if Docker's iptables blocks the netns bridges, use
  the same temporary allowance T4a used and say so), static-no-broker.sh 0 violations, and a new static check
  docs/cleanup-2026-10/static-no-fallback.sh that greps for the specific patterns you removed (so they cannot come
  back) with an allow-list + reasons. Write docs/cleanup-2026-10/T4b.md (changes one line each with why; each gate with
  command + result). Final message: 6 lines.
