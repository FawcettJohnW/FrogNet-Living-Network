#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# /usr/local/bin/sync_interfaces.sh - FrogNet discovery + route install
#
# Model (John's, this session):
#   * Discovery proves every path as a .2/32: install <dest>.2/32 via the
#     .2 next-hop (LAN) or plain dev (wg), echo through the full semantic
#     stack, measure RTT, tear the /32 down.  .2 throughout discovery so a
#     probe NEVER touches the live .1 production path.
#   * Discovery next-hop is .2.  Production next-hop is .1.  The via is
#     ALWAYS the destination network's own gateway (convertToShortIP): .2
#     while proving, .1 once promoted.  No third-party relay address is
#     ever a via - the owning node answers ARP for its own .1/.2 on
#     whatever segment receives the request, and wg AllowedIPs=10/8 carries
#     any 10.x on the tunnel dev.
#   * Nothing is installed at /24 until a winner exists.  After the walk,
#     per /24 the lowest-RTT proven candidate is PROMOTED to the durable
#     /24 (via .1) at metric 22, the .2/32 admin alias rides the same .1
#     at metric 5, and the slower proven candidates become the 100+
#     failover ladder.
#   * Promotion rewrites the .2 discovery forms to the .1 production forms,
#     BUT a production .1 route is written only if it is absent or differs
#     from the kernel.  `ip route replace` on an already-correct live route
#     tears it down and drops packets, so correct live routes are left
#     untouched (compare-then-act, deltas only).
#
# Discovery walk: depth-2, bidirectional (DHCP/ARP/tunnel children down,
# client-interface .1s up).  Each node is gated on its OWN echo to .2
# before its path is recorded - a parent listing a child in getHosts is
# not proof, so the child is proven directly.  Dead/reflected wg ifaces
# are excluded first so they never yield a false candidate.
#
# Removed vs the wave/committer build: wave batching, getHosts vouching,
# WAVE2_ALTERNATIVES, observation/commit-final indirection (this script
# installs the winners itself), probe-failure route removal.
# -----------------------------------------------------------------------------

. /usr/local/lib/frognet_log.sh
flog_init "sync_interfaces"
log() { flog_info "$*"; }

# --- [DIAG-ROUTE] route-lifecycle instrumentation -----------------------------
# Additive only: nothing here changes routing behavior. Every mutation runs the
# SAME ip command as before, but logs it with rc + an immediate read-back of the
# affected dest, so a write that "succeeds" yet is shadowed/removed is visible.
# Tag matches the proxy/daemon [DIAG-WRITER]/[DIAG-READER] convention; ts is raw
# epoch so these lines correlate directly with the proxy's t=... traces.
_droute_ts() { printf '%s' "${EPOCHREALTIME:-$(date +%s.%N)}"; }
# RTMUT route <verb> <dest> [args...]
RTMUT() {
    local ts spec verb dest rc present
    ts="$(_droute_ts)"; spec="$*"; verb="$2"; dest="$3"
    $IP "$@" 2>/dev/null; rc=$?
    present="$($IP -o -4 route show "$dest" 2>/dev/null | $TR '\n' ';')"
    log "[DIAG-ROUTE] MUTATE ts=$ts caller=${FUNCNAME[1]:-?} verb=$verb rc=$rc dest=$dest spec=\"ip $spec\" after=\"${present:-ABSENT}\""
    return $rc
}
# rt_snapshot <label>: full 10.x main table + unicast 10.x across ALL tables.
rt_snapshot() {
    local label="$1" ts line n=0
    ts="$(_droute_ts)"
    log "[DIAG-ROUTE] SNAP-BEGIN label=$label ts=$ts"
    while IFS= read -r line; do log "[DIAG-ROUTE] SNAP label=$label main $line"; n=$((n+1)); done \
        < <($IP -o -4 route show 2>/dev/null | $GREP -E '^10\.')
    while IFS= read -r line; do log "[DIAG-ROUTE] SNAP label=$label all  $line"; done \
        < <($IP -o -4 route show table all 2>/dev/null | $GREP -E '10\.' | $GREP -vE '^(local|broadcast|multicast)')
    log "[DIAG-ROUTE] SNAP-END label=$label count=$n ts=$ts"
}

log "ENTER"
log "[DIAG-ROUTE] BUILD tag=route-diag-v1 base=$(basename "$0")"

# ---- Binaries --------------------------------------------------------------
IP="/usr/sbin/ip"
CURL="/usr/bin/curl"
AWK="/usr/bin/awk"
CUT="/usr/bin/cut"
HEAD="/usr/bin/head"
SORT="/usr/bin/sort"
TR="/usr/bin/tr"
JQ="/usr/bin/jq"
GREP="/usr/bin/grep"
MKTEMP="/usr/bin/mktemp"
XARGS="/usr/bin/xargs"
ALIVE="/usr/local/bin/frognet_alive.bash"
ADD_HOST="/usr/local/bin/addHostAndPropogate.bash"
SUBNET="/usr/local/bin/convertToSubnetRange"
DISCOVERY_CACHE="/usr/local/bin/frognet_discovery_cache.sh"
PYTHON="/usr/bin/python3"
LEASES="/var/lib/misc/dnsmasq.leases"

# ---- Paths -----------------------------------------------------------------
SENT_DIR="/etc/sentinels"
ECHO_CACHE="${SENT_DIR}/echo_cache"
DISCOVERED_HOSTS="${SENT_DIR}/discovered_hosts"
TUNNELS_DEAD_SENTINEL="${SENT_DIR}/tunnels_dead"
HANDSHAKE_RTTS_PATH="/var/lib/frognet-tunnel/handshake_rtts.json"
TUNNEL_STATE_DIR="/var/lib/frognet-tunnel"
mkdir -p "$SENT_DIR"

BRINGUP_READY="${SENT_DIR}/tunnel_bringup_ready"
BRINGUP_DONE="${SENT_DIR}/tunnel_bringup_done"
BRINGUP_MAX_WAIT_SEC="${FROGNET_BRINGUP_MAX_WAIT_SEC:-30}"

# ---- Config ----------------------------------------------------------------
MAX_DEPTH=2
ECHO_TIMEOUT="${FROGNET_DISCOVERY_ECHO_TIMEOUT:-15}"
GETHOSTS_TIMEOUT="${FROGNET_GETHOSTS_TIMEOUT:-30}"

PROBE_METRIC=6      # transient .2/32 discovery route. Swept on entry+exit.
WINNER_METRIC=22    # promoted /24 winner.
ALIAS_METRIC=5      # .2/32 admin alias paired with the winner.
FALLBACK_BASE=100   # slower proven candidates: 100, 101, ...

# ---- Per-run working state -------------------------------------------------
: > "$ECHO_CACHE"
: > "$DISCOVERED_HOSTS"
: > "$TUNNELS_DEAD_SENTINEL"
PROBE_DIR="$($MKTEMP -d /tmp/frognet_discovery.XXXXXX)"

# CAND[dest24] = newline-joined "rtt|via|dev|onlink|src|kind|host"
declare -A CAND
declare -A WALK_SEEN

# -----------------------------------------------------------------------------
# Transient .2/32 discovery-route sweep (entry + exit).  ONLY metric 5 -
# never touches winner (22), alias (5), or fallback (100+) production routes.
# -----------------------------------------------------------------------------
_sweep_probe_routes() {
    local cidr n=0
    while IFS= read -r cidr; do
        [[ -n "$cidr" ]] || continue
        RTMUT route del "$cidr" && n=$((n+1))
    done < <($IP -4 route show 2>/dev/null | $AWK '/^10\./ && /metric 5/ {print $1}')
    flog_info "PROBE_SWEEP" "swept=$n"
}
trap 'rc=$?; rt_snapshot exit_pre_sweep; _sweep_probe_routes; rt_snapshot exit_post_sweep; rm -rf "$PROBE_DIR"; log "EXIT rc=$rc"; exit $rc' EXIT INT TERM
flog_stage probe_entry_sweep; _sweep_probe_routes; flog_stage_end probe_entry_sweep

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
is_ip()  { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
is_10x() { [[ "$1" == 10.* ]]; }

mapfile -t LOCAL_IPS < <($IP -4 -o addr show | $AWK '{print $4}' | $CUT -d/ -f1 | $SORT -u)
declare -A LOCAL_SUBNETS
for lip in "${LOCAL_IPS[@]}"; do LOCAL_SUBNETS["${lip%.*}"]=1; done
is_local_ip()        { for l in "${LOCAL_IPS[@]}"; do [[ "$1" == "$l" ]] && return 0; done; return 1; }
is_on_local_subnet() { [[ -n "${LOCAL_SUBNETS[${1%.*}]}" ]]; }

net_dot()   { echo "${1%.*}.$2"; }          # net_dot 10.x.y.z N -> 10.x.y.N
dest24_of() { echo "${1%.*}.0/24"; }

dev_src() {  # src hint: wg dev's own 10.x addr; empty for LAN
    [[ "$1" == wg* ]] || { echo ""; return; }
    $IP -4 -o addr show dev "$1" 2>/dev/null | $AWK '{print $4}' | $HEAD -n1 | $CUT -d/ -f1
}

channel_for_iface() {
    local want="$1" f iface ch
    for f in /var/lib/frognet-tunnel/active/*.json; do
        [[ -f "$f" ]] || continue
        iface="$($JQ -r '.interface // empty' "$f" 2>/dev/null)"
        ch="$($JQ -r '.channel_name // empty' "$f" 2>/dev/null)"
        [[ "$iface" == "$want" && -n "$ch" ]] && { echo "$ch"; return; }
    done
    echo ""
}
broker_for_peer_ip() {
    local ip="$1" net
    [[ -n "$ip" && -f "$HANDSHAKE_RTTS_PATH" ]] || return 1
    net="$("$SUBNET" "$ip" 2>/dev/null || true)"
    $JQ -r --arg ip "$ip" --arg net "$net" '
        to_entries[] | select(.value.peer_dot_one == $ip)
        | [.key, .value.subnet, .value.peer_dot_one] | @tsv
    ' "$HANDSHAKE_RTTS_PATH" 2>/dev/null | $HEAD -n 1
}

measure_rtt() {        # frognet_alive -> "<ms>|<method>"; empty on failure
    local target="$1" result ms
    result=$("$ALIVE" "$target" 3 2>/dev/null) || true
    if [[ -n "$result" ]]; then
        ms="${result%%|*}"; ms="${ms%%.*}"
        [[ -n "$ms" ]] && { [[ "$ms" == "0" ]] && echo 1 || echo "$ms"; return; }
    fi
    echo ""
}

list_active_devs() {
    $IP -o link show | $AWK -F': ' '{print $2}' | $CUT -d'@' -f1 | while read -r d; do
        [[ "$d" == "lo" || "$d" == veth* || "$d" == docker* || "$d" == br-* || "$d" == virbr* ]] && continue
        if $IP link show dev "$d" | $GREP -qE "state (UP|UNKNOWN)"; then echo "$d"
        elif [[ -n "$($IP -4 -o addr show dev "$d" 2>/dev/null)" ]]; then echo "$d"; fi
    done | $SORT -u
}
seed_from_dev_ip() {
    local dev="$1" cidr ip4 prefix
    cidr="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1)"
    [[ -n "$cidr" && "$cidr" == 10.*/* && "$cidr" != 10.253.*/* ]] || return 0
    prefix="${cidr#*/}"; [[ "$prefix" -le 24 ]] || return 0
    ip4="${cidr%/*}"; echo "$ip4" | $AWK -F. '{print $1"."$2"."$3".1"}'
}
PING_BIN="$(command -v ping 2>/dev/null || echo /bin/ping)"
lease_neighbor_live() {
    local dev="$1" ip="$2" line
    line="$($IP -4 neigh show "$ip" dev "$dev" 2>/dev/null)"
    case "$line" in *FAILED*|*INCOMPLETE*) ;; *lladdr*) return 0 ;; esac
    "$PING_BIN" -c1 -W1 -I "$dev" "$ip" >/dev/null 2>&1
}
_wait_for_bringup() {
    local waited=0
    while (( waited < BRINGUP_MAX_WAIT_SEC )); do
        [[ -f "$BRINGUP_READY" || -f "$BRINGUP_DONE" ]] && return 0
        sleep 1; waited=$((waited+1))
    done
    return 1
}

# -----------------------------------------------------------------------------
# .2/32 discovery-route install / delete.
#   LAN: <pip>/32 via <pip> dev <dev> onlink metric 5   (pip = the dest .2)
#   wg : <pip>/32 dev <dev> metric 5                     (AllowedIPs=10/8)
# -----------------------------------------------------------------------------
# probe_install pip dev disc_via
#   disc_via empty (tunnel) -> <pip>/32 dev wgN              (AllowedIPs=10/8)
#   disc_via set    (LAN)   -> <pip>/32 via <disc_via> dev <dev> onlink
# disc_via: the dest's own .2 (DIRECT) or the parent forwarder's .1 (RELAY).
probe_install() {
    local pip="$1" dev="$2" disc_via="$3" src; src="$(dev_src "$dev")"
    if [[ -z "$disc_via" ]]; then
        RTMUT route replace "$pip/32" dev "$dev" metric "$PROBE_METRIC" ${src:+src "$src"}
    else
        RTMUT route replace "$pip/32" via "$disc_via" dev "$dev" onlink metric "$PROBE_METRIC"
    fi
}
probe_delete() { RTMUT route del "$1/32" metric "$PROBE_METRIC" || true; }

echo_probe() {         # echo the dest .2 through whatever /32 is installed
    local pip="$1" a raw code body cand
    for a in 1 2 3; do
        raw=$($CURL -sS --connect-timeout 2 --max-time "$ECHO_TIMEOUT" \
                   -H "Host: $pip" -w $'\n__HTTP_CODE__:%{http_code}' \
                   "http://$pip/frognet_echo.php" 2>/dev/null)
        code="${raw##*__HTTP_CODE__:}"
        body="${raw%__HTTP_CODE__:*}"; body="${body%$'\n'}"; body="${body//$'\r'/}"; body="${body//$'\n'/}"
        [[ "$raw" != *__HTTP_CODE__:* ]] && code=""
        cand="${body//\\n/}"
        if [[ -n "$cand" && "$cand" =~ ^[A-Za-z0-9._-]+,10\.[0-9]+\.[0-9]+\.[0-9]+,[0-9.]*,[0-9.]*$ ]]; then
            echo "$cand"; return 0
        fi
        sleep 1
    done
    return 1
}

# route_matches dest via dev metric onlink(0/1)
#   0 = an entry for dest at this metric already matches exactly (LEAVE IT)
#   1 = absent or differs (write it)
route_matches() {
    local dest="$1" w_via="$2" w_dev="$3" w_metric="$4" w_onlink="$5" line
    local c_via c_dev c_metric c_onlink
    while IFS= read -r line; do
        c_metric="$($AWK '{for(i=1;i<=NF;i++) if($i=="metric") print $(i+1)}' <<<"$line")"
        [[ "$c_metric" == "$w_metric" ]] || continue
        c_via="$($AWK '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1)}' <<<"$line")"
        c_dev="$($AWK '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' <<<"$line")"
        c_onlink=0; [[ "$line" == *" onlink"* ]] && c_onlink=1
        [[ "$c_via" == "$w_via" && "$c_dev" == "$w_dev" && "$c_onlink" == "$w_onlink" ]] && return 0
        return 1
    done < <($IP -o -4 route show "$dest" 2>/dev/null)
    return 1
}

# install_if_changed dest via dev metric onlink src
# Writes ONLY if absent or different; a correct live route is left untouched.
install_if_changed() {
    local dest="$1" via="$2" dev="$3" metric="$4" onlink="$5" src="$6"
    if route_matches "$dest" "$via" "$dev" "$metric" "$onlink"; then
        local _cur; _cur="$($IP -o -4 route show "$dest" 2>/dev/null | $TR '\n' ';')"
        log "[DIAG-ROUTE] KEEP dest=$dest via=$via dev=$dev metric=$metric reason=already_correct cur=\"${_cur:-ABSENT}\""
        return 0
    fi
    local -a argv=("route" "replace" "$dest")
    [[ -n "$via" ]] && argv+=("via" "$via")
    argv+=("dev" "$dev" "metric" "$metric")
    [[ "$onlink" == "1" && -n "$via" ]] && argv+=("onlink")
    [[ -n "$src" ]] && argv+=("src" "$src")
    if RTMUT "${argv[@]}"; then
        flog_info "ROUTE_INSTALL" "dest=$dest via=$via dev=$dev metric=$metric onlink=$onlink"
    else
        flog_warn "ROUTE_INSTALL_FAILED" "dest=$dest via=$via dev=$dev metric=$metric"
    fi
}

# =============================================================================
#  TUNNEL HEALTH - exclude dead/reflected wg before any probing.
# =============================================================================
_health_probe_tunnel() {
    local iface="$1" peer_ip="$2" ch="$3" out="$4" body code self_ip raw
    RTMUT route replace "$peer_ip/32" dev "$iface" metric "$PROBE_METRIC" \
        || { echo "iface=$iface ch=$ch reason=route_install_failed" > "$out"; return; }
    raw=$($CURL -sS --connect-timeout 2 --max-time "$ECHO_TIMEOUT" -H "Host: $peer_ip" \
               -w $'\n__HTTP_CODE__:%{http_code}' "http://$peer_ip/frognet_echo.php" 2>/dev/null)
    code="${raw##*__HTTP_CODE__:}"; body="${raw%__HTTP_CODE__:*}"; body="${body%$'\n'}"; body="${body//$'\r'/}"; body="${body//$'\n'/}"
    [[ "$raw" != *__HTTP_CODE__:* ]] && code=""
    RTMUT route del "$peer_ip/32" metric "$PROBE_METRIC" || true
    if [[ "$code" != "200" || -z "$body" ]]; then
        echo "iface=$iface ch=$ch reason=echo_failed_code=${code:-none}_body_len=${#body}" > "$out"; return
    fi
    IFS=',' read -r _node self_ip _l1 _l2 <<<"$body"; self_ip="$($XARGS <<<"$self_ip")"
    [[ "$self_ip" != "$peer_ip" ]] && echo "iface=$iface ch=$ch reason=wrong_peer_self_ip=${self_ip:-none}_expected=$peer_ip" > "$out"
}
run_tunnel_health_check() {
    flog_stage tunnel_health_check
    declare -gA DEAD_IFACES=()
    local -a pids=(); local -A outs=() meta=()
    local jf iface ch subnet0 base peer_ip out pid m dev
    for jf in /var/lib/frognet-tunnel/active/*.json; do
        [[ -f "$jf" ]] || continue
        iface="$($JQ -r '.interface // empty' "$jf" 2>/dev/null)"
        ch="$($JQ -r '.channel_name // empty' "$jf" 2>/dev/null)"
        subnet0="$($JQ -r '.remote_subnets[0] // empty' "$jf" 2>/dev/null)"
        [[ -n "$iface" && -n "$ch" && -n "$subnet0" ]] || continue
        $IP link show dev "$iface" >/dev/null 2>&1 || continue
        base="${subnet0%.0/24}"; peer_ip="${base}.1"; is_ip "$peer_ip" || continue
        out="$PROBE_DIR/tunhealth.${iface}.out"; outs["$iface"]="$out"; meta["$iface"]="${ch}|${peer_ip}"
        _health_probe_tunnel "$iface" "$peer_ip" "$ch" "$out" & pids+=("$!")
    done
    for pid in "${pids[@]:-}"; do wait "$pid" 2>/dev/null || true; done
    for iface in "${!outs[@]}"; do
        out="${outs[$iface]}"; m="${meta[$iface]}"; ch="${m%%|*}"; peer_ip="${m##*|}"
        if [[ -s "$out" ]]; then
            DEAD_IFACES["$iface"]=1; flog_warn "TUNNEL_DEAD" "$(cat "$out")"; echo "$ch" >> "$TUNNELS_DEAD_SENTINEL"
        else flog_info "TUNNEL_HEALTHY" "iface=$iface ch=$ch peer_ip=$peer_ip"; fi
        rm -f "$out" 2>/dev/null || true
    done
    if (( ${#DEAD_IFACES[@]} > 0 )); then
        local -a keep=()
        for dev in "${ALL_DEVS[@]}"; do [[ -n "${DEAD_IFACES[$dev]:-}" ]] && continue; keep+=("$dev"); done
        ALL_DEVS=("${keep[@]}")
        log "Active interfaces (after health filter): ${ALL_DEVS[*]}"
    fi
    flog_stage_end tunnel_health_check "dead=${#DEAD_IFACES[@]}" "checked=${#outs[@]}"
}

# =============================================================================
#  walk - depth-2 discovery.  Prove this node's .2 path, record a candidate,
#  getHosts, recurse children.  NO /24 installed here.
# =============================================================================
walk() {
    local dev="$1" ip="$2" depth="$3" parent_net="$4"   # parent_net = the proven
                                                          # forwarder's .1 ("" for seeds)
    # [SEGRELAY_INHERIT_V1] The on-segment address of the node we descended
    # THROUGH to reach this dest ("" at depth 1).  A dest more than one hop
    # from us is not on any of our connected subnets, so the guard below
    # cannot compute a seg_relay for it - but the packet still enters the
    # segment through the same on-segment address that carried the parent,
    # and that node forwards onward.  Inheriting it keeps SEGRELAY on the
    # ballot at depth >= 2, where previously only DIRECT and RELAY (via the
    # parent's .1, onlink) were offered.
    local inherited_relay="${5:-}"
    local wkey="${dev}|${ip}"

    [[ -n "$dev" ]] && is_ip "$ip" && is_10x "$ip" || return
    [[ "$ip" == 10.254.* ]] && return                 # chorus is on-link, not a /24 target
    is_local_ip "$ip" && return                        # that's us
    # Skip admin-alias (.2) addresses appearing on our own segments; the node's
    # .1 (or getHosts) is the discovery handle for it.
    is_on_local_subnet "$ip" && [[ "${ip##*.}" == "2" ]] && return
    [[ -n "${WALK_SEEN[$wkey]}" ]] && return
    WALK_SEEN["$wkey"]=1
    # NB: the shared failure cache is NOT consulted here.  runMerge flushes it
    # so every merge re-probes everything; a getHosts child a live parent is
    # vouching for must be probed regardless of a stale mesh-wide failure.  The
    # echo is the proof.  We do not write failures either (threshold=1 on the
    # shared DB turns one transient miss into a mesh-wide skip).

    local kind="lan"; [[ "$dev" == wg* ]] && kind="tunnel"

    # ---- identity acquisition --------------------------------------------
    # A FrogNet node can appear on one of OUR segments as a DHCP/ARP client at
    # a non-.1 address while hosting a DIFFERENT /24 (the eth0/DHCP pathway).
    # Its on-segment address says nothing about its hosted net, and deriving a
    # .2 from it would hit OUR own admin alias.  So echo the address directly
    # (the connected route already reaches it) to learn host_path, and keep the
    # on-segment address as the relay it forwards its own net through.
    local host_path="" seg_relay="$inherited_relay"   # [SEGRELAY_INHERIT_V1]
    if [[ "$kind" == "lan" ]] && is_on_local_subnet "$ip" && [[ "${ip##*.}" != "1" ]]; then
        local pre
        if ! pre="$(echo_probe "$ip")"; then
            flog_info "WALK" "dev=$dev ip=$ip decision=NOT_FROGNET reason=on_segment_client_no_echo"
            return
        fi
        IFS=',' read -r _ host_path _ _ <<<"$pre"
        host_path="$(echo "$host_path" | $TR -d ' ')"
        is_ip "$host_path" && is_10x "$host_path" || { flog_info "WALK" "dev=$dev ip=$ip decision=BAD_IDENTITY"; return; }
        seg_relay="$ip"
        # A client hosting one of our OWN subnets (or itself) gives nothing to route.
        if is_on_local_subnet "$host_path"; then
            flog_trace "WALK" "dev=$dev ip=$ip host_path=$host_path decision=DEST_LOCAL"; return
        fi
    fi

    # Destination network: client -> its hosted net; otherwise the seed IS the .1.
    local dest_seed="${host_path:-$ip}"
    local pip; pip="$(net_dot "$dest_seed" 2)"          # discovery target: the dest .2
    flog_info "WALK" "dev=$dev ip=$ip depth=$depth kind=$kind probe=$pip parent_net=${parent_net:-none} seg_relay=${seg_relay:-none}"

    # ---- candidate next-hops, in order.  Each: "label|disc_via" ----------
    #   DIRECT   - via the dest's own .2 (owner answers ARP for its own .2 on dev).
    #   SEGRELAY - via the node's on-segment client address (it forwards into its
    #              hosted net); next-hop is on our connected subnet, so no onlink.
    #   RELAY    - via the proven parent's .1 (the address we reached the parent
    #              at). The primer (sim sync_interfaces, wave-2) routes a peer's
    #              known host via=peer_iface_ip and trusts the peer to forward;
    #              this matches the production via below (also parent .1). The
    #              probe target is still the dest's .2, so a transient dest.2/32
    #              never clobbers the dest's .1 production /24.
    #   TUNNEL   - dev-only (AllowedIPs=10/8); no via.
    local -a cands=()
    if [[ "$kind" == "tunnel" ]]; then
        cands=( "TUNNEL|" )
    else
        cands=( "DIRECT|$pip" )
        [[ -n "$seg_relay"  ]] && cands+=( "SEGRELAY|$seg_relay" )
        [[ -n "$parent_net" ]] && cands+=( "RELAY|$(net_dot "$parent_net" 1)" )
    fi

    local chosen_label="" disc_via echo_line="" rtt=""
    local cand label
    for cand in "${cands[@]}"; do
        label="${cand%%|*}"; disc_via="${cand#*|}"
        probe_install "$pip" "$dev" "$disc_via" || { flog_trace "WALK" "dev=$dev pip=$pip label=$label decision=probe_route_failed"; continue; }
        if echo_line="$(echo_probe "$pip")"; then
            chosen_label="$label"
            rtt="$(measure_rtt "$pip")"
            break                                      # keep this /32 up for getHosts
        fi
        probe_delete "$pip"                            # this form didn't carry it; try next
        echo_line=""
    done
    if [[ -z "$chosen_label" ]]; then
        flog_info "WALK" "dev=$dev ip=$ip pip=$pip decision=FAIL_ECHO tried=${cands[*]}"
        return
    fi

    local host hp2
    IFS=',' read -r host hp2 _ _ <<<"$echo_line"
    host="$(echo "$host" | $TR -d ' ')"; hp2="$(echo "$hp2" | $TR -d ' ')"
    [[ -n "$hp2" ]] && is_ip "$hp2" && is_10x "$hp2" && host_path="$hp2"
    if ! is_local_ip "$ip" && is_local_ip "$host_path"; then
        flog_warn "WALK" "dev=$dev ip=$ip decision=DNAT_LOOPBACK returned_local=$host_path"
        probe_delete "$pip"; return
    fi

    # [BROKER_AUTHORITY] broker is authoritative for identity over a peer
    # self-report (rename/migration).
    local dest1 b_ch b_sub b_one
    dest1="$(net_dot "$host_path" 1)"
    IFS=$'\t' read -r b_ch b_sub b_one < <(broker_for_peer_ip "$dest1") || true
    if [[ -n "$b_sub" && -n "$b_one" ]] && is_ip "$b_one" && is_10x "$b_one"; then
        host_path="$b_one"; host="${b_ch%-*.*.*}"
        if [[ "$dev" == wg* ]]; then
            local iface_ch; iface_ch="$(channel_for_iface "$dev")"
            if [[ -n "$iface_ch" && "$iface_ch" != "$b_ch" ]]; then
                flog_warn "WALK_REJECT" "dev=$dev ip=$ip dev_channel=$iface_ch broker_channel=$b_ch reason=wg_channel_mismatch"
                probe_delete "$pip"; return
            fi
        fi
    fi

    # Production via for the winning form: DIRECT -> dest's own .1; RELAY -> the
    # parent forwarder's .1; TUNNEL -> none (dev route).  .2 proves it, .1 carries it.
    local dest24 src p_via p_onlink
    dest24="$(dest24_of "$host_path")"
    src="$(dev_src "$dev")"
    case "$chosen_label" in
        TUNNEL)   p_via="";                           p_onlink=0 ;;
        DIRECT)   p_via="$(net_dot "$host_path" 1)";  p_onlink=1 ;;
        SEGRELAY) p_via="$seg_relay";                 p_onlink=0 ;;
        RELAY)    p_via="$(net_dot "$parent_net" 1)"; p_onlink=1 ;;
    esac

    [[ -n "$rtt" ]] || rtt=999999
    CAND["$dest24"]+="${rtt}|${p_via}|${dev}|${p_onlink}|${src}|${kind}|${host}"$'\n'
    flog_info "CANDIDATE" "dest=$dest24 via=$p_via dev=$dev onlink=$p_onlink rtt=$rtt kind=$kind form=$chosen_label host=$host"

    printf '%s\t%s\n' "$host_path" "$echo_line" >> "$ECHO_CACHE"
    echo "$host_path" >> "$DISCOVERED_HOSTS"
    "$ADD_HOST" "$host" "$host_path" "" "" "$dev" || true
    # Positive evidence only (used by Seed 4 and cross-node vouch); never write failures.
    if [[ -x "$DISCOVERY_CACHE" ]] && is_on_local_subnet "$ip" && [[ "$ip" != 10.253.* ]]; then
        "$DISCOVERY_CACHE" success "$ip" "" "$dev" "$host" "$host_path" "$rtt" || true
    fi

    if (( depth < MAX_DEPTH )); then
        local hf="$PROBE_DIR/hosts_${pip//./_}" hcode="" child a
        for a in 1 2 3; do
            hcode=$($CURL -sS -o "$hf" -w '%{http_code}' --connect-timeout 2 --max-time "$GETHOSTS_TIMEOUT" \
                    -H "Host: $host_path" "http://$pip/getHosts.php" 2>/dev/null) || hcode=""
            [[ "$hcode" == "200" ]] && break
            sleep 1
        done
        probe_delete "$pip"
        if [[ "$hcode" == "200" && -f "$hf" ]]; then
            # Children forward through THIS node: pass its .1 as their relay next-hop.
            while read -r child; do
                [[ -n "$child" ]] || continue
                [[ "$child" == "$host_path" ]] && continue
                # [SEGRELAY_INHERIT_V1] 5th arg: the on-segment address that
                # carried us to this parent.  Empty at depth 1 for a dest that
                # is itself a .1 on our segment; set for a DHCP-leased client.
                walk "$dev" "$child" "$((depth+1))" "$host_path" "${seg_relay:-}"
            done < <($JQ -r '.[]?.ip // empty' "$hf" 2>/dev/null)
        fi
        rm -f "$hf"
        return
    fi
    probe_delete "$pip"
}

# =============================================================================
#  promote - proven .2 candidates become production .1 routes.  Winner
#  (lowest RTT) -> /24 @22 + .2/32 alias @5; slower distinct-dev candidates
#  -> 100+ ladder.  Every write is delta-only.
# =============================================================================
promote() {
    flog_stage promote
    local _d
    for _d in "${!CAND[@]}"; do
        log "[DIAG-ROUTE] CAND dest=$_d lines=\"$(printf '%s' "${CAND[$_d]}" | $GREP -v '^$' | $TR '\n' ';')\""
    done
    local dest lines dest2 rtt via dev onlink src kind host fb rank
    for dest in "${!CAND[@]}"; do
        lines="$(printf '%s' "${CAND[$dest]}" | $GREP -v '^$' | $SORT -t'|' -k1,1n)"
        dest2="$(net_dot "${dest%/*}" 2)"
        declare -A used_dev=()
        rank=0
        while IFS='|' read -r rtt via dev onlink src kind host; do
            [[ -n "$dev" ]] || continue
            [[ -n "${used_dev[$dev]:-}" ]] && continue      # one route per dev per dest
            used_dev["$dev"]=1
            if (( rank == 0 )); then
                install_if_changed "$dest"     "$via" "$dev" "$WINNER_METRIC" "$onlink" "$src"
                install_if_changed "$dest2/32" "$via" "$dev" "$ALIAS_METRIC"  "$onlink" "$src"
                flog_info "PROMOTE_WINNER" "dest=$dest via=$via dev=$dev rtt=$rtt"
            else
                fb=$((FALLBACK_BASE + rank - 1))
                install_if_changed "$dest" "$via" "$dev" "$fb" "$onlink" "$src"
                flog_info "PROMOTE_FALLBACK" "dest=$dest via=$via dev=$dev rtt=$rtt metric=$fb"
            fi
            rank=$((rank+1))
        done <<< "$lines"
        unset used_dev
    done
    flog_stage_end promote
}

# =============================================================================
#  push_transits_to_broker - register the LAN /24s we PROVED reachable this
#  run.  Restores the walk-driven transit feed dropped by the V2 lease-only
#  rewrite of discover_transit_subnets: every kind=lan candidate in CAND is a
#  /24 we relay to, so the broker must install namespace forwarding for it or
#  tunneled peers can't reach LANs behind us.  Successful echo IS the proof -
#  same signal promote() trusts to install the route.
#
#  Best-effort: failures log and continue, never fail the merge.  Idempotent
#  on the broker (returns "unchanged" when the set already matches).
#  CAND line layout: rtt|via|dev|onlink|src|kind|host  (kind is field 6).
# =============================================================================
push_transits_to_broker() {
    local dest minrtt
    local -a transit=()
    local rttmap=""                      # "cidr":rtt,...  (additive RTT hints)
    for dest in "${!CAND[@]}"; do
        # Min RTT among this /24's kind=lan candidates; empty if none are lan.
        # Lowest wins so a consumer can prefer the nearest relay when more than
        # one node advertises the same transit /24.
        minrtt="$(printf '%s' "${CAND[$dest]}" | $GREP -v '^$' \
            | $AWK -F'|' '$6=="lan"{ if (m=="" || $1<m) m=$1 } END{ if (m!="") print m }')"
        [[ -n "$minrtt" ]] || continue   # not a LAN-relay target
        transit+=("$dest")
        rttmap+="\"$dest\":$minrtt,"
    done
    if (( ${#transit[@]} == 0 )); then
        flog_info "TRANSIT_PUSH" "transits=[] reason=no_lan_candidates"
        return
    fi
    rttmap="${rttmap%,}"

    # Broker identity.  NB(JOHN): the new sync_interfaces sources neither of
    # these today - this mirrors the old push_transits_to_broker.  Confirm
    # against the live tree: (1) tunnel.conf path + that it defines BROKER_URL,
    # (2) that wg0's pubkey is the node's broker identity (matches config.PUBKEY
    # the daemon registers with). If the daemon uses a different key/URL source,
    # point these two lines at it instead.
    local conf=/etc/frognet/tunnel.conf
    [[ -r "$conf" ]] && . "$conf"
    local pubkey; pubkey="$(wg show wg0 public-key 2>/dev/null)"
    if [[ -z "${BROKER_URL:-}" || -z "$pubkey" ]]; then
        flog_warn "TRANSIT_PUSH" \
            "skipped reason=missing_broker_url_or_pubkey transits=[${transit[*]}]"
        return
    fi

    local csv="" s
    for s in $(printf '%s\n' "${transit[@]}" | $SORT -u); do csv+="\"$s\","; done
    csv="${csv%,}"
    # transit_subnets stays a bare string list (existing contract); RTTs ride
    # along additively in transit_rtts so current consumers don't break.
    local body="{\"pubkey\":\"$pubkey\",\"transit_subnets\":[$csv],\"transit_rtts\":{$rttmap}}"
    local resp
    resp="$($CURL -sk --max-time 15 -X POST -H 'Content-Type: application/json' \
                  -d "$body" "${BROKER_URL}/api/v4/update-subnets" 2>/dev/null)"
    flog_info "TRANSIT_PUSH" "transits=[$csv] rtts={$rttmap} resp=${resp:-none}"
}

# =============================================================================
#  MAIN
# =============================================================================
flog_stage bringup_barrier
if _wait_for_bringup; then flog_info "bringup_barrier" "status=cleared"
else flog_warn "bringup_barrier" "status=timeout" "waited_s=$BRINGUP_MAX_WAIT_SEC"; fi
flog_stage_end bringup_barrier

log "SEEDS_BEGIN"
rt_snapshot enter
mapfile -t ALL_DEVS < <(list_active_devs)
log "Active interfaces: ${ALL_DEVS[*]}"
run_tunnel_health_check

# ---- DOWNSTREAM: DHCP leases, disk cache, tunnel peers, kernel wg /24s, ARP
flog_stage descend_downstream
if [[ -f "$LEASES" && -s "$LEASES" ]]; then
    while read -r ip; do
        [[ -n "$ip" && "$ip" == 10.* ]] || continue
        local_pfx="${ip%.*}"; placed=0
        for dev in "${ALL_DEVS[@]}"; do
            dev_ip="$($IP -4 -o addr show dev "$dev" | $AWK '{print $4}' | $HEAD -n1 | $CUT -d/ -f1)"
            [[ -n "$dev_ip" ]] || continue
            if [[ "${dev_ip%.*}" == "$local_pfx" ]]; then
                walk "$dev" "$ip" 1   # echo (retried) is the liveness test; a 1s
                                      # ping/cold ARP must not veto a live node
                placed=1; break
            fi
        done
        [[ $placed -eq 1 ]] && continue
        for dev in "${ALL_DEVS[@]}"; do
            [[ "$dev" == wg* || "$dev" == frognet0 || "$dev" == wl* || "$dev" == lo ]] && continue
            log "LEASE_SEED_FALLBACK dev=$dev ip=$ip"; walk "$dev" "$ip" 1
        done
    done < <($AWK '{print $3}' "$LEASES" | $SORT -u)
fi
if [[ -x "$DISCOVERY_CACHE" ]]; then
    while IFS=$'\t' read -r peer_ip _h _hp cached_dev; do
        [[ -n "$peer_ip" ]] || continue
        is_on_local_subnet "$peer_ip" && [[ "$peer_ip" != 10.253.* ]] && walk "$cached_dev" "$peer_ip" 1
    done < <("$DISCOVERY_CACHE" list 10)
fi
if [[ -d "$TUNNEL_STATE_DIR" ]]; then
    for state in "${TUNNEL_STATE_DIR}/active/"*.json; do
        [[ -f "$state" ]] || continue
        iface="$($JQ -r '.interface // empty' "$state")"; [[ -n "$iface" ]] || continue
        [[ -n "${DEAD_IFACES[$iface]:-}" ]] && continue
        $IP link show "$iface" >/dev/null 2>&1 || continue
        while read -r subnet; do
            [[ -n "$subnet" && "$subnet" == 10.* ]] || continue
            gw="${subnet%.*}.1"; is_local_ip "$gw" && continue
            log "TUNNEL_SEED iface=$iface subnet=$subnet gw=$gw"
            walk "$iface" "$gw" 1
        done < <($JQ -r '.remote_subnets[]? // empty' "$state" 2>/dev/null)
    done
fi
for dev in "${ALL_DEVS[@]}"; do
    [[ "$dev" == wg* ]] || continue
    while read -r net_cidr; do
        [[ "$net_cidr" == 10.*.0/24 && "$net_cidr" != 10.253.*.0/24 && "$net_cidr" != 10.254.*.0/24 ]] || continue
        gw="${net_cidr%%.0/24}.1"; is_local_ip "$gw" && continue
        log "WG_ROUTE_SEED dev=$dev subnet=$net_cidr gw=$gw"
        walk "$dev" "$gw" 1
    done < <($IP route show dev "$dev" scope link 2>/dev/null | $AWK '{print $1}')
done
for dev in "${ALL_DEVS[@]}"; do
    [[ "$dev" == wg* || "$dev" == frognet0 || "$dev" == lo ]] && continue
    while IFS= read -r neigh_ip; do
        [[ -n "$neigh_ip" && "$neigh_ip" == 10.* ]] || continue
        [[ "$neigh_ip" == 10.253.* || "$neigh_ip" == 10.254.* ]] && continue
        is_local_ip "$neigh_ip" && continue
        log "ARP_SEED dev=$dev neigh=$neigh_ip"
        walk "$dev" "$neigh_ip" 1
    done < <($IP -4 neigh show dev "$dev" 2>/dev/null | $AWK '$0 ~ /lladdr/ && $0 !~ /FAILED/ {print $1}')
done
flog_stage_end descend_downstream

# ---- UPSTREAM: the .1 of every interface where we are a client ----
flog_stage descend_upstream
for dev in "${ALL_DEVS[@]}"; do
    s="$(seed_from_dev_ip "$dev")"
    [[ -n "$s" ]] && walk "$dev" "$s" 1
done
flog_stage_end descend_upstream

log "SEEDS_END candidates_for=${#CAND[@]}"

# ---- promote winners; the ONLY place /24s are written ----
promote
rt_snapshot post_promote

# Register LAN /24s we proved reachable this run (walk-driven transit feed).
push_transits_to_broker

flog_info "DISCOVERY_DONE"
exit 0
