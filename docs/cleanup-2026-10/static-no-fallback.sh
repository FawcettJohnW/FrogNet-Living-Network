#!/bin/bash
# static-no-fallback.sh - T4b/T4c static gate: the second mechanisms T4b and T4c removed cannot come back.
#
#   bash docs/cleanup-2026-10/static-no-fallback.sh          # from anywhere; finds the repo root from its own path
#
# John's rule: a failed step is logged and retried on the next event, never replaced by a second mechanism.
# Each PATTERN below is one of the specific fallbacks T4b (and, from region-sources on, T4c; from monitor-local-ip on, T5) removed (id, ERE, what it was). git grep over tracked +
# untracked files in etc/ usr/ var/ opt/frognet_semantic opt/fdf, binary files skipped, excluding *.md, files named
# test_*, *.diff, opt/frognet_semantic/docs and opt/fdf/src/fdf-source/docs.
# Every match is printed. A match is ALLOWED only by an ALLOW entry (pattern id + path glob + line regex + reason);
# anything else is a VIOLATION. Exit 0 = no violation; exit 1 = at least one.

set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 2

SCOPE=(etc usr var opt/frognet_semantic opt/fdf)
EXCL=(':(exclude)opt/frognet_semantic/docs' ':(exclude,glob)**/*.md' ':(exclude,glob)**/test_*'
      ':(exclude)opt/fdf/src/fdf-source/docs' ':(exclude,glob)**/*.diff' ':(exclude)opt/fdf/dist'
      ':(exclude)opt/frognet_semantic/ribbit/cpp/tests/reference' ':(exclude)opt/frognet_semantic/ribbit/examples/lispers.net/artifacts'
      ':(exclude,glob)**/third_party/**')

# PATTERNS: "id<TAB>ERE<TAB>what it was"
PATTERNS=$(cat <<'EOF'
alive-alt	HOSTS\+=\( "\$\{prefix\}\.[12]" \)	frognet_alive.bash probing .1 when .2 did not answer (and .2 for .1)
returnip-identity	gethostname\(\), "FrogNetHost"\)	_get_alive_return_ip: /etc/hosts identity, then 0.0.0.0, when the dev had no 10.x
propagate-hosts	etc_hosts_fallback|legacy /etc/hosts \.1 scan	propogateNotificationInternal: every 10.x.y.1 in /etc/hosts when discovery.neighbors failed
hosts-fallback	falling back to /etc/hosts|/etc/hosts fallback failed	monitor discover_hosts (and runPingEverything): /etc/hosts when getHosts.php failed
public-dns	8\.8\.8\.8|8\.8\.4\.4|GOOGLE_DNS	a public resolver / ping target hard-coded (resolv.conf always ended 8.8.8.8; fdf-facts pinged it)
nm-activation	[Tt]ry multiple activation methods|Forcing IP assignment|Reconnecting \$ETH0|Direct activation failed	setup_lillypad: conn up, conn up ifname, disconnect/reconnect, flush+ip addr add
db-unit-chain	systemctl[^|;]* mysql(\.service)?( |$|")	`mariadb || mysql`: a second database unit tried after the first
gw-echo	getEth0Address[^|]*\|\| *echo "?\$GATEWAY_IP	getEth0Address || echo $GATEWAY_IP
eth0-fixup	frognet_eth0_fixup|frognet-eth0-fixup	frognet-eth0-fixup: a boot-time second owner of the LAN addresses (flush, ip addr add, nmcli conn up)
belt	[Bb]elt[- ]and[- ]suspenders	a second write of the same state "to be sure"
unbound-chain	FIX_UNBOUND|\|\| *\{ *systemctl stop unbound	`fix_unbound_bypass.sh || systemctl stop unbound`
py-fallback	\|\| *[A-Za-z_]*(PY|PYBIN|PYTHON)[A-Za-z_]*="?/usr/bin/python3	the venv's python, else /usr/bin/python3
system-pip	--break-system-packages	pip into the system python (the services run on the venv)
apt-chain	\|\| *(DEBIAN_FRONTEND=[a-z]+ )?apt-get install	install_databasehost: a chain of package lists
facts-swallow	stderr=subprocess\.DEVNULL, timeout=10	fdf-facts sh(): every failure returned "" with stderr discarded
boot-cron	@reboot sleep [0-9]+ *&& */usr/local/bin/runMerge	@reboot sleep 240 && runMerge.bash (the hook is the boot merge)
legacy-tail	FROGNET_MERGE_LEGACY_TAIL	runMerge's legacy tail switch
wg-fallback	FDF_WG_USERSPACE|r is None or r\.returncode != 0|userspace only when the kernel refuses	WireGuard: kernel first, wireguard-go when the kernel refused
unmanaged-ap	frognet-unmanaged-ap\.conf	the installer's permanent NM unmanaged for the AP radio beside netstart's per-mode decision
tune-twin	frognet_system_tune\.sh	the cmp-identical second copy of frognet-system-tune.sh
eth0-by-name	i\.dev == "eth0"|"\$dev" != "\$\{eth0Name:-eth0\}"|dev != eth0_name	an interface's role decided by its name (the uplink is eth0 on a container or a one-NIC box)
built-in-pond	165\.232\.153\.144|RINA_AREA:-[a-z]	a built-in region address or area name in shipped runtime
region-sources	environ\.get\("RINA_(RAM|AREA)"|conf\.get\("RINA_|join\.env['"]?,? *['"]?/etc/frognet/tunnel\.conf|join\.env /etc/frognet/tunnel\.conf|_read\("/etc/frognet/tunnel\.conf"\)	T4c: the region switch read from tunnel.conf / the environment / join.env (join.env is the one file; site.env the area)
eth0-dhcp-range	dhcp-range" /etc/dnsmasq\.d/opts_only\.conf|infer from dnsmasq dhcp-range	T4c: getEth0Address answering from dnsmasq's dhcp-range when the LAN dev had no 10.x
wg-teardown-alt	"link", "del(ete)?", iface	T4c: `ip link del` of a wgN after (or instead of) `wg-quick down`; the daemon makes them with wg-quick
hook-fork	MERGE_WHY="nm:|is-active --quiet frognet-merge-watcher	T4c: the NetworkManager hook running runMerge itself when the watcher was not running
watcher-region-off	frognet-dbhost-elect\.timer frognet-merge-watcher	T4c: fdf-region-join stopping the merge watcher (and fdf-leave starting it again)
watcher-poll	inotifywait .*"\$REQ_DIR" 2>/dev/null	T4c: the merge watcher's inotifywait with its failure discarded (no inotify-tools: a silent 1 s poll, no window)
monitor-local-ip	parse_local_ip_from_ip_output	the monitor's own address: the first 10.x in `ip addr`, else 127.0.0.1 (T5: the snapshot names this node's .1)
dbhost-resolver	gethostbyname\('databasehost\.frognet'\)	getDbHost.php: the databasehost from the resolver (its name when unresolved) beside /etc/hosts (T5)
dbhost-alias	includes\("databasehost"\)	fln_dashboard32: the databasehost from a Topology sensor's LOCAL alias, a second source beside getDbHost (T5)
stale-picture	Transient empty (hosts|edges) snapshot	fln_dashboard32: an empty host list replaced by the previous picture (T5)
fetch-variants	jgetTry|all fetch variants failed	fln_dashboard32: try one URL, then the next (T5; it had no caller)
monitor-hosts-file	fopen\("/etc/hosts"|ReadAllLinesAsync\("/etc/hosts"|def _hosts_lookup	the C++/C#/Python monitors reading /etc/hosts for the databasehost themselves (T5: the snapshot's elections)
old-monitor	/etc/hosts primary	var/www/html/frognet_monitor (v5.1): /etc/hosts first, getHosts.php as an 'optional upgrade' (T5: deleted)
nm-identity	ip4 "\$\{GATEWAY_IP\}/24|nmcli dev(ice)? set "\$ETH0" managed yes	2026-10-14: the identity as a NetworkManager profile beside frognet-netstart (setup_lillypad, frognet_fixup.sh)
EOF
)

# ALLOW: "id<TAB>path-glob<TAB>line-regex (ERE; . = any)<TAB>reason"
ALLOW=$(cat <<'EOF'
*	usr/local/bin/frognet_prune_removed.sh	.	Names what it removes (its T3 section, with the T4b block at its end, is the installer's upgrade step).
*	*	ALIVE_ONE_TARGET_V1|RETURN_IP_DEV_ONLY_V1|NEIGHBORS_ONLY_V1|MONITOR_HOSTS_ONE_SOURCE_V1|RESOLV_LOCAL_UPSTREAM_V1|NM_OWNS_THE_LAN_ADDRESS_V1|ONE_DB_UNIT_V1|UNBOUND_ONE_STEP_V1|SERVICES_ON_THE_VENV_V1|ONE_PACKAGE_LIST_V1|SH_FAILURES_SAID_V1|UPLINK_JUDGED_LOCALLY_V1|BOOT_MERGE_IS_THE_HOOK_V1|WG_IMPL_RECORDED_V1|AP_MANAGED_BY_NETSTART_V1|ONE_TUNE_SCRIPT_V1|UPLINK_BY_FUNCTION_V1|PONDS_ARE_THE_OPERATORS_V1|NO_BUILT_IN_AREA_V1|REGION_SWITCH_ONE_FILE_V1|LAN_DEV_ONLY_V1|WG_TEARDOWN_BY_ITS_OWNER_V1|HOOK_ASKS_THE_WATCHER_V1|ONE_MONITOR_TRUTH_V1|NETSTART_OWNS_THE_IDENTITY_V1|WIRED_LAN_IS_NOT_THE_SERVED_LAN_V1|AP_UNMANAGED_PERSISTS_V1|NETSTART_SCOPES_DHCP_V1	A T4b/T4c/T5 tag line: it records what went and that it is gone.
public-dns	usr/local/bin/frognet_metrics_emit.sh	-4 -j route get 8\.8\.8\.8	A kernel route LOOKUP (which device the default route uses); no packet is sent, nothing is asked of that address.
public-dns	usr/local/bin/manageResolv.bash	no hardcoded 8\.8\.8\.8	The doctrine line [DNS_NEXTHOP_ONLY_V1] saying there is none.
public-dns	usr/local/bin/installer/frognet_reset.sh	used to write 8\.8\.8\.8	History: what the reset no longer writes (T4a).
public-dns	etc/dnsmasq.d/upstream_fallback.conf	forwarding to 8\.8\.8\.8	History: [NO_DNS_FALLBACK_V1], why no-resolv must not be set.
public-dns	opt/frognet_semantic/DOCTRINE.txt	John, 2026-09-26, with 8\.8\.8\.8 last	The doctrine's dated note that the 8.8.8.8 went.
public-dns	opt/frognet_semantic/discovery/resolv.py	it replaces \[RESOLV_LOCAL_UPSTREAM_GOOGLE_V1\]	History: the tag [RESOLV_LOCAL_UPSTREAM_V1] replaced.
public-dns	opt/frognet_semantic/oracles/*	.	Oracle fixtures (recorded node files, not runtime).
public-dns	opt/frognet_semantic/simulation/*	.	Simulator topologies and fixtures (an "internet" address in a model).
public-dns	opt/frognet_semantic/discovery/sim/*	.	Discovery simulator fixtures.
public-dns	opt/fdf/src/fdf-source/rinab/experiments/*	.	Recorded experiments (retired), not shipped.
public-dns	opt/frognet_semantic/ribbit/examples/*	.	Example programs and their recorded runs (not the node runtime).
public-dns	opt/frognet_semantic/agent_workload/*	.	The PyTorch workload (node-local, not in the repository's shipped set).
public-dns	opt/fdf/src/frognet-android/*	.	The Android/Windows member sources (an app rebuild; see T4b.md).
db-unit-chain	usr/local/bin/installer/frognet_reset.sh	it was "start mariadb \|\| start mysql \|\| true"	History comment on the one-unit line.
db-unit-chain	usr/local/bin/installer/frognet_install.sh	mysql-server|mysql\.sock|mysqladmin	Not a unit: the distribution's file names.
eth0-fixup	usr/local/bin/setup_lillypad_v4.bash	frognet-eth0-fixup \(Before=network.target	History: what Step 11 no longer creates.
eth0-fixup	usr/local/bin/installer/frognet_install.sh	frognet-eth0-fixup gone	The CORE_SERVICES note.
belt	usr/local/bin/installer/frognet_install.sh	"belt-and-suspenders"\), which contradicted	History: what E6a(4) no longer writes.
belt	opt/frognet_semantic/discovery/probe_sweep.py	ceiling is the belt-and-suspenders	A wall-clock ceiling on a wedged probe cell (a timeout), not a second mechanism for the same state.
belt	opt/frognet_semantic/frognet_route/committer.py	Sweeps any leaked /32 probes	The committer's sweep of probe routes it installed itself (its own cleanup), not a second owner.
belt	usr/local/bin/frognet_make_build_tar.sh	Identity files \(belt-and-suspenders	An exclusion from a build tar (never ship a node's identity), not a second mechanism.
unmanaged-ap	usr/local/bin/installer/frognet_install.sh	unmanaged for good	History: what E6a(4) no longer writes.
tune-twin	usr/local/bin/installer/frognet_install.sh	cmp-identical frognet_system_tune.sh is gone	History: the F7 note.
system-pip	opt/frognet_semantic/codex_fullsuite/*|opt/frognet_semantic/simulation/spec_compliance/*	html5lib	A developer note for an optional test-corpus dependency of the spec-compliance suite (not a node service).
py-fallback	opt/frognet_semantic/simulation/*	.	Simulator harness helpers (they run on the developer's python, not a node service).
wg-fallback	opt/frognet_semantic/simulation/netns_backend.py	was \[WG_USERSPACE_FALLBACK_V1\]	History: the tag this replaced.
built-in-pond	opt/fdf/src/frognet-android/app/src/main/kotlin/com/fawcettinnovations/fdf/android/Identity.kt	165\.232\.153\.144	Android default pond: changing it needs an app rebuild (listed in T4b.md, not done here).
built-in-pond	opt/frognet_semantic/discovery/sim/*	.	Simulator fixtures (the real fleet's addresses as test data).
built-in-pond	opt/fdf/src/fdf-source/rinab/experiments/*	.	Recorded experiments (retired), not shipped.
built-in-pond	opt/fdf/src/fdf-source/rinab/tools/map-*.sh|opt/fdf/src/fdf-source/rinab/tools/*-check.sh	.	Test harness scripts (their own scratch areas), not shipped runtime.
eth0-by-name	opt/frognet_semantic/simulation/*	.	Simulator models name their interfaces.
eth0-by-name	opt/frognet_semantic/discovery/sim/*	.	Simulator models name their interfaces.
region-sources	opt/fdf/bin/fdf-hub|opt/fdf/src/fdf-source/fdf/bin/fdf-hub|opt/fdf/src/fdf-source/rinab/tools/fdf-hub	.	FD&F's hub: RINA_RAM is its one input, from its unit (the region holder's hub.env); no file behind it.
region-sources	opt/fdf/lib/python/fdfram.py|opt/fdf/src/fdf-source/fdf/lib/python/fdfram.py|opt/fdf/windows/fdfram.py|opt/fdf/bin/fdf-reach|opt/fdf/src/fdf-source/fdf/bin/fdf-reach	.	FD&F operator tools (not the tunnel daemon): the environment, then join.env / the pond file. Outside T4c's brief; listed in T4c.md "Found, not done".
region-sources	opt/frognet_semantic/simulation/s4_dist.py	"RINA_RAM": os\.environ\.get\("RINA_RAM"\)	The simulator's per-machine diagnostic record: it WRITES what the environment held into the run's report; nothing reads the region from it.
EOF
)

allowed_by() {   # allowed_by <id> <path> <line> -> prints the reason, returns 0 if allowed
    local id="$1" path="$2" line="$3" i g re why gg
    while IFS=$'\t' read -r i g re why; do
        [[ -z "$i" ]] && continue
        [[ "$i" == "*" || "$i" == "$id" ]] || continue
        local hit=1
        IFS='|' read -r -a globs <<<"$g"
        for gg in "${globs[@]}"; do
            # shellcheck disable=SC2053
            [[ "$path" == $gg ]] && { hit=0; break; }
        done
        [[ $hit -eq 0 ]] || continue
        if [[ "$re" == "." ]] || grep -q -E -- "$re" <<<"$line"; then
            printf '%s' "$why"; return 0
        fi
    done <<<"$ALLOW"
    return 1
}

viol=0; ok=0
declare -A why_count=() per_id=()
while IFS=$'\t' read -r id re what; do
    [[ -z "$id" ]] && continue
    n=0
    while IFS= read -r m; do
        [[ -z "$m" ]] && continue
        path="${m%%:*}"; rest="${m#*:}"; text="${rest#*:}"
        n=$((n+1))
        if why="$(allowed_by "$id" "$path" "$text")"; then
            ok=$((ok+1)); why_count[$why]=$(( ${why_count[$why]:-0} + 1 ))
            printf 'ALLOWED   [%s] %s\n' "$id" "${m:0:180}"
        else
            viol=$((viol+1))
            printf 'VIOLATION [%s] %s\n        (%s)\n' "$id" "${m:0:180}" "$what"
        fi
    done < <(git grep --untracked -n -I -E -e "$re" -- "${SCOPE[@]}" "${EXCL[@]}" 2>/dev/null)
    per_id[$id]=$n
done <<<"$PATTERNS"

echo
echo "matches per pattern:"
while IFS=$'\t' read -r id _ _; do [[ -n "$id" ]] && printf '  %-18s %s\n' "$id" "${per_id[$id]:-0}"; done <<<"$PATTERNS"
echo "allowed, by reason:"
for w in "${!why_count[@]}"; do printf '  %4d  %s\n' "${why_count[$w]}" "${w:0:150}"; done | sort -rn
echo
echo "static-no-fallback: $ok allowed, $viol violation(s)"
[[ $viol -eq 0 ]]
