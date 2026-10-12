#!/bin/bash
# static-no-broker.sh - T3b static gate: nothing in the shipped runtime talks about, to, or for a broker.
#
#   bash docs/cleanup-2026-10/static-no-broker.sh            # from anywhere; finds the repo root from its own path
#
# What it searches (git grep over tracked + untracked, binary files skipped):
#   -i 'broker'   and, case-sensitive,   BROKER_URL | GROUP_TOKEN | PASSCODE | POND_PASSWORD | /api/v4 |
#                                        frognet_chorus | pond_bootstrap
# Where: etc/ usr/ var/ opt/frognet_semantic opt/fdf
#   excluding opt/frognet_semantic/docs, *.md, files named test_*, opt/fdf/src/fdf-source/docs and *.diff.
#
# Every match is printed. A match is ALLOWED only by an entry in ALLOW below (path glob + line regex + reason);
# anything else is a VIOLATION. Exit 0 = no violation; exit 1 = at least one.

set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 2

SCOPE=(etc usr var opt/frognet_semantic opt/fdf)
EXCL=(':(exclude)opt/frognet_semantic/docs' ':(exclude,glob)**/*.md' ':(exclude,glob)**/test_*'
      ':(exclude)opt/fdf/src/fdf-source/docs' ':(exclude,glob)**/*.diff')

# ALLOW: "path-glob<TAB>line-regex (ERE)<TAB>reason". The path glob is a bash pattern over the repo-relative path.
ALLOW=$(cat <<'EOF'
opt/frognet_semantic/simulation/frognet_sim.py	.	TIER 1 model (run_all "model suite"). Its World.broker_channels is the channel list merge_cycle consumes, and ~40 regression scenarios replay broker-era incidents (stale entries, phantom channels, poll-vs-kernel drift) by name. Re-modelling it on the region is not small; it is a model, not shipped runtime.
usr/local/bin/frognet_prune_removed.sh	.	Names the paths and keys it removes (its T3 section is the installer's upgrade step).
opt/frognet_semantic/ribbit/cpp/tests/reference/*	.	Frozen reference copy of the 2026-08 Python engine: the oracle ribbit/cpp/qualify.sh runs the C++ port against. Editing it changes the oracle.
opt/frognet_semantic/ribbit/examples/lispers.net/artifacts/*	.	Recorded output of past run_all runs (evidence files), not code.
*	NO_BROKER_V1|BROKER_TEARDOWN_V1|BROKER_AUTHORITY	A tag that records the removal; each such line says what went and that it is gone (T3a/T3b).
*	[Bb]roker[Hh]ost	A machine's name (the host once called BrokerHost / brokerhost.seattle2) in history notes and test fixtures.
opt/frognet_semantic/internet_tunnels_v3/config.py	broker's BROKER_URL, GROUP_TOKEN, POND_PASSWORD	The daemon's docstring naming the old keys it ignores (it logs them once, [NO_BROKER_V1]).
opt/frognet_semantic/oracles/oracle_routing_topology.py	"broker" not in payload|BROKER_URL=https://broker.invalid|only an old BROKER_URL	The oracle asserts there is no broker field and that an old BROKER_URL alone means LAN-only.
opt/fdf/src/fdf-source/rinab/tools/netns-lan-only-test.sh	.	The LAN-only netns oracle: it plants old broker keys and asserts they are named once, ignored, and that nothing asks a broker anything.
opt/fdf/src/fdf-source/rinab/tools/netns-region-loop-test.sh	broker keys in tunnel.conf	The region oracle's header: its nodes carry no old broker keys.
opt/fdf/bin/fdf-hub	Replaces frognet_broker_v4 for a region	History: what the hub replaced (the hub's docstring).
opt/fdf/src/fdf-source/fdf/bin/fdf-hub	Replaces frognet_broker_v4 for a region	History: the same docstring (cmp-identical source copy).
opt/fdf/src/fdf-source/rinab/tools/fdf-hub	Replaces frognet_broker_v4 for a region	History: the same docstring (cmp-identical copy make_deliverables checks).
opt/fdf/bin/fdf-facts	broker-mode tunnels|re-registered with the broker	History: the incident that defined [HUB_ROW_IS_THE_REGION_EDGE_V1].
opt/fdf/src/fdf-source/fdf/bin/fdf-facts	broker-mode tunnels|re-registered with the broker	History: the same comment (cmp-identical source copy).
opt/fdf/src/fdf-source/rinab/src/rina_region.cpp	10.252.254.0/24 is the broker's veths	C++ source of the built RAM manager: why 10.252.254.0/24 is never handed to a leaf (an address reservation).
opt/fdf/src/fdf-source/rinab/experiments/exp3-topologies.json	no internet, no broker	Recorded experiment input: frognet_sim's topology label, carried verbatim.
opt/frognet_semantic/simulation/baselines/topo_mixed_iface_no_router.json	no internet, no broker	frognet_sim's learned baseline; the label must equal the model topology's name.
EOF
)

allowed_by() {   # allowed_by <path> <line> -> prints the reason, returns 0 if allowed
    local path="$1" line="$2" g re why
    while IFS=$'\t' read -r g re why; do
        [[ -z "$g" ]] && continue
        # shellcheck disable=SC2053
        [[ "$path" == $g ]] || continue
        if [[ "$re" == "." ]] || grep -q -E -- "$re" <<<"$line"; then
            printf '%s' "$why"; return 0
        fi
    done <<<"$ALLOW"
    return 1
}

matches() {
    git grep --untracked -n -I -i -e 'broker' -- "${SCOPE[@]}" "${EXCL[@]}"
    git grep --untracked -n -I -E -e 'BROKER_URL|GROUP_TOKEN|PASSCODE|POND_PASSWORD|/api/v4|frognet_chorus|pond_bootstrap' \
        -- "${SCOPE[@]}" "${EXCL[@]}"
}

viol=0; ok=0
declare -A seen=() why_count=()
while IFS= read -r m; do
    [[ -n "${seen[$m]:-}" ]] && continue; seen[$m]=1
    path="${m%%:*}"; rest="${m#*:}"; text="${rest#*:}"
    if why="$(allowed_by "$path" "$text")"; then
        ok=$((ok+1)); why_count[$why]=$(( ${why_count[$why]:-0} + 1 ))
        printf 'ALLOWED   %s\n' "${m:0:200}"
    else
        viol=$((viol+1))
        printf 'VIOLATION %s\n' "${m:0:200}"
    fi
done < <(matches)

echo
echo "allowed, by reason:"
for w in "${!why_count[@]}"; do printf '  %4d  %s\n' "${why_count[$w]}" "${w:0:150}"; done | sort -rn
echo
echo "static-no-broker: $ok allowed, $viol violation(s)"
[[ $viol -eq 0 ]]
