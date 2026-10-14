# 2026-10-14: Seattle7 lost its identity (r21)

**Symptom.** Seattle7 is wireless mode: eth0 has no carrier, and the AP is wlan0. On it, `setup_lillypad_v4.bash` died at Step 6 with "No suitable device found for this connection (connection type is not loopback)". After that the node had no .1/.2, and netstart failed FATAL with "No dhcp-range found".

**Causes. All four were found in the T0 tree as well as in r20.**
1. setup_lillypad took eth0Name from mapInterfaces. In wireless mode, netstart's `active_interface` pin makes that the AP radio. So lillypad built an *ethernet* NM profile on wlan0, and NM refused it.
2. lillypad's Step 3 ran `nmcli dev set $ETH0 managed yes`, which handed the AP radio to NM. NM joined it to a saved WiFi network as a client: wlan0 got 10.250.250.155 by DHCP.
3. lillypad's Step 1b purges opts_only.conf, and Step 9 writes it again. A death at Step 6 therefore left no dhcp-range, so netstart could not run.
4. `frognet-ssid-projection` rewrote ssid_projection.conf with SSID_PROJECTION only. That dropped AP_IFACE and WIFI_COUNTRY, and the installer's E7 runs it on every install.

Also found:
- When netstart ran before NM was up (cron @reboot), `managed no` had nothing to tell. NM then took the radio once it started.
- The DHCP scope was fixed at whichever device lillypad saw when it ran. After a mode change, DHCP was refused on the new LAN device.
- Step 12 wrote the radio into transit_exclude_interfaces, which left the bare eth0 open to become a transit link.

**Fix.**
- `[NETSTART_OWNS_THE_IDENTITY_V1]`: lillypad makes no NM identity profile and no `managed yes`. It writes the config and then runs frognet-netstart (Step 11), unless `--norestart` is given, in which case cron @reboot runs it. The installer already marked the wired LAN NM-unmanaged, so the old profile never came up at boot anyway.
- `[WIRED_LAN_IS_NOT_THE_SERVED_LAN_V1]`: mapInterfaces exports wiredLanName, which comes from detection plus interfaces_override.conf and never from active_interface. lillypad uses it for Step 2 and Step 12.
- `[AP_UNMANAGED_PERSISTS_V1]`: in wireless mode netstart writes `/etc/NetworkManager/conf.d/99-frognet-wireless-ap.conf` (`unmanaged-devices+=`), and in wired mode it removes that file. It also waits up to 30 s for NM while NM is still activating.
- `[NETSTART_SCOPES_DHCP_V1]`: every netstart run rewrites the `no-dhcp-interface=` lines for the device it just chose.
- `[SSID_SWITCH_KEEPS_THE_CONF_V1]`: the switch changes the one line and leaves the rest of the file alone.
- If hostapd will not restart in wireless mode, netstart now dies and logs it, instead of carrying on with no AP.
- `frognet_fixup.sh` is removed and pruned on upgrade. It was the April clone-repair script: a second setup_lillypad that also wrote the identity (flush, `ip addr add`, an NM profile).
- Build ids: frognet-install-2026-10-14-r21, frognet-netstart-2026-10-14-r21, setup-lillypad-2026-10-14-r21.
- static-no-fallback now has the pattern `nm-identity`.

**Checked.**
- `bash -n` on each changed script.
- static-no-fallback: 0 violations. static-no-broker: 0. check-docs: 0.
- The oracles give the same results as r20.
- Container G1, LAN-only:
  - Install EXIT 0, "All checks passed".
  - After a reboot, netstart put 10.61.61.1/.2 on eth1 and logged "DHCP scoped to eth1". There was no FrogNet NM profile.
  - A live `setup_lillypad_v4.bash G1 10.61.61.1` gave rc 0, with identity on eth1.
  - After `ip addr flush dev eth1`, a lillypad run restored both addresses.
  - `frognet-ssid-projection on` then `off` kept AP_IFACE and WIFI_COUNTRY.
- **Not checked:** the wireless path. The container has no radio. Seattle7 is that test.

## r22: the run on Seattle7 (06:36)
r21 worked on Seattle7:
- 10.170.170.1/.2 are on wlan0.
- "DHCP scoped to wlan0".
- DecoPond1 was taken down and wlan0 unmanaged.
- The merge's IDENTITY_PREFLIGHT passed.

The same log also shows `Runtime mode: false`, an hour after boot. netstart decided boot vs runtime with `systemctl is-system-running` = running|degraded, but the system stays "starting" until the first merge completes: frognet-discovered.service blocks multi-user.target on it. On a node whose merge cannot run, which is the case netstart is there to repair, every run therefore skipped three things while still logging "hostapd started":
- the hostapd restart;
- the dnsmasq restart;
- the merge.

At a real boot it never started hostapd either.

Fixes:
- `[NETSTART_ALWAYS_APPLIES_V1]`: the guess is gone. Wired mode stops hostapd. Wireless mode restarts it, and netstart dies if that fails. dnsmasq is restarted, and netstart dies if that fails.
- `[HOOK_ASKS_THE_WATCHER_V1]`: the merge is requested from the watcher through /run/frognet/merge_requests, the same way the NetworkManager hook asks for one, instead of running `runMerge.bash &`.
- `[NETSTART_ONE_AT_A_TIME_V1]`: cron @reboot and ifplugd ran netstart side by side at boot. A flock now makes the second run wait for the first.

Build ids: frognet-netstart-2026-10-14-r22, frognet-install-2026-10-14-r22.

Container G1:
- Install EXIT 0, "All checks passed".
- Two reboots, each giving eth1 .1/.2, "DHCP scoped to eth1", "dnsmasq restarted" and "merge requested".
- The two boot runs are serialized.
- The system reaches `running`, with no failed unit.

## r23: code on a node that was never in the repository
On Seattle7, run_all failed 3 discovery tests that the tree passes 84/84:
- test_transit_chain_oracle imports FakeBroker;
- test_license_oracle reports 338 files with no notice;
- test_not_frognet_skip_oracle fails.

The node carries code that no repository version has had, such as `discovery/test_transit_chain_oracle.py` and `simulation/sim_games_over_wire.py`. frognet_prune_removed.sh can only name what the cleanup deleted from the repository, so it never touched these files.

`[CODE_IS_THE_TREE_V1]`: `frognet_prune_removed.sh --sweep-code` removes every .py/.sh/.bash/.php under opt/frognet_semantic that the install record (/var/lib/frognet-install/files, written at C1) does not list.
- Data files are not touched.
- The node-state dirs are left alone: venv, docs, logs, blob_cache, etc, bin, usr, var, _v1.
- With no record, it says so and counts that as a failure.

The installer's C1c passes `--sweep-code`. frognet_build_release.sh does not, because on a build host new code exists in /opt/frognet_semantic before any install has recorded it. /usr/local/bin is not swept, because it holds pip console scripts and other projects' tools.

Installer build id: frognet-install-2026-10-14-r23.

Checked:
- A fake root with a record: removed the 2 strays and kept the recorded code, venv/, logs/ and DB_CONFIG.json.
- run_all on the tree: discovery 84/84, clean-install oracle PASS.
- static-no-fallback: 0 violations.

## r24: the r23 sweep deleted node-local code
`/opt/frognet_semantic/agent_workload/` holds the PyTorch collectives and DDP hooks. It lives on the nodes and has never been in any repository, so it was not in the install record, and the r23 sweep removed its .py files.

`[SWEEP_ONLY_RELEASE_DIRS_V1]`: the sweep now removes a file only if it sits in a directory the install record has files in. agent_workload/ is also excluded by name. A directory the release does not ship (agent_workload/, anything you add of your own) and top-level files are left alone.

Fake-root test:
- Removed: discovery/test_transit_chain_oracle.py and simulation/sim_games_over_wire.py.
- Kept: agent_workload/tuplespace/ddp_hook.py, mywork/thing.py, stray_top.py, venv/, logs/ and DB_CONFIG.json.

Build id: frognet-install-2026-10-14-r24.
