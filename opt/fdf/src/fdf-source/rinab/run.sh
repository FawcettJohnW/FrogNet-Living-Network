#!/bin/bash
# run.sh RLITE_ROOT [FIRST LAST] -- everything, in one command, as root:
#   1. checks: root, the rlite tree, the tools; nothing already running; the port is free
#   2. builds the host and rlite's programs (tools/build.sh, tools/build_rlite.sh)
#   3. starts the RINA host ISOLATED (own pid + mount namespaces: it can see no test program or device) on its own
#      port, and checks it answers before any test starts
#   4. runs rlite's integration tests FIRST..LAST (default 1..30), one at a time, each with a fresh agent
#   5. if a test is still running after HANG_S seconds (default 15), the live host's threads are dumped with gdb
#      at that moment -- the evidence of what it is stuck on -- and the test is left to finish or time out
#   6. prints every result, and for each failure the end of its log, the host's log, and any hang dump
#
#   RLITE_ROOT  the rlite source tree (the one with tests/integration and include/rina)
#   HOST_LISTEN=127.0.0.1  the address the host listens on (its RLOC when its area is registered with LISP)
#   LISP_MS=a.b.c.d LISP_AREA=name LISP_KEY=k  the host registers its area name with this Map-Server at start ([AREA_NAME_IS_A_LISP_EID_V1])
#   PORT=8840   the host's port      HANG_S=15  when to dump a slow test's host      TEST_S=120  a test's limit
#
# Everything a run produces is under ./runs/<date-time>/ and named at the end. Nothing is written outside rinab/
# except the namespaces and veths rlite's own tests create (and remove).
set -u
ROOT=${1:?usage: ./run.sh RLITE_ROOT [FIRST LAST]}; FIRST=${2:-1}; LAST=${3:-30}
PORT=${PORT:-8840}; HOST_LISTEN=${HOST_LISTEN:-127.0.0.1}; HANG_S=${HANG_S:-15}; TEST_S=${TEST_S:-120}
H=$(cd "$(dirname "$0")" && pwd); B=$H/build; API=/FawcettInnovations.RINA.ram_interface.php
R=$H/runs/$(date +%Y%m%d-%H%M%S); mkdir -p "$R"
say() { echo "run.sh: $*"; }
die() { echo "run.sh: STOP: $*" >&2; exit 2; }

# ---- 1. checks -----------------------------------------------------------------------------------------------------
[ "$(id -u)" = 0 ] || die "run as root (the tests create network namespaces)"
ROOT=$(cd "$ROOT" 2>/dev/null && pwd) || die "no directory $1"
[ -d "$ROOT/tests/integration" ] && [ -d "$ROOT/include/rina" ] || die "$ROOT is not the rlite source tree (no tests/integration or include/rina)"
for t in g++ gcc protoc pkg-config ip unshare gdb ethtool; do command -v $t >/dev/null || die "missing command: $t"; done
for p in rina-host rina-ram rlite-uipcps rinaperf rina-echo-async iporinad; do
    pgrep -x "$p" >/dev/null && die "a $p is already running: $(pgrep -ax "$p" | head -3 | tr '\n' ';') -- stop it first"; done
[ -z "$(ip netns list)" ] || die "network namespaces already exist ($(ip netns list | awk '{print $1}' | tr '\n' ' ')) -- rlite's tests create their own; delete leftovers first (ip netns delete NAME)"
ss -ltn "sport = :$PORT" | grep -q LISTEN && die "port $PORT is in use: $(ss -ltnp "sport = :$PORT" | tail -n +2) -- free it or set PORT="
ulimit -c unlimited
say "open-file limit for the host: $(ulimit -n)"

# ---- 2. build --------------------------------------------------------------------------------------------------------
say "building (log: $R/build.log)"
{ "$H/tools/build.sh" && "$H/tools/build_rlite.sh" "$ROOT"; } > "$R/build.log" 2>&1 || { tail -30 "$R/build.log"; die "build failed"; }
grep -E "BUILD OK|^built" "$R/build.log"

# ---- 3. the host -----------------------------------------------------------------------------------------------------
# [PRIMED_TEMPLATES_V1] both ends learn the primed store at start (PRIME=0 to learn on the wire instead)
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")" && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")" && pwd)/templates/rina.prime
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime
export PATH=$B/bin:$PATH RINA_RAM=$HOST_LISTEN:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
# [REGION_FILE_V1] the region's Permanent records (a DIF's policy and parameters); each run starts from its own
export RINA_REGION_FILE=${RINA_REGION_FILE:-$R/region.perm}
mkdir -p /var/run/netns
unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec $B/bin/rina-host --listen $HOST_LISTEN:$PORT --api $API" > "$R/host.log" 2>&1 &
UNSHARE=$!
stop_all() { pkill -x rinaperf; pkill -x rina-echo-async; pkill -x iporinad; pkill -x rlite-uipcps; pkill -x rina-host; wait $UNSHARE 2>/dev/null; }
trap stop_all EXIT
for i in $(seq 100); do (exec 3<>/dev/tcp/$HOST_LISTEN/$PORT) 2>/dev/null && break; sleep 0.05; done
HOSTPID=$(pgrep -nx rina-host) || { cat "$R/host.log"; die "the host did not start"; }
rlite-ctl > /dev/null || die "the host is up (pid $HOSTPID) but rlite-ctl cannot use it"
say "host pid $HOSTPID on $HOST_LISTEN:$PORT, isolated; clients connect from $RINA_RAM_NETNS"
# [AREA_NAME_IS_A_LISP_EID_V1] the host registers its area's name -> its RLOC, once (the registration's own TTL; re-registered
# only when the host changes). Its Map-Notify is the proof the mapping system holds it; no Map-Notify, no run.
if [ -n "${LISP_MS:-}" ]; then
    rina-lisp register "${LISP_AREA:?LISP_AREA}" "$HOST_LISTEN" "$LISP_MS" "${LISP_KEY:?LISP_KEY}" 86400 || die "the host could not register its area with the Map-Server $LISP_MS"
fi
# [AREA_MAPPING_IN_THE_REGION_V1] MAP_AREA=name MAP_KEY=k: FD&F's own mapping, in a region over the session (no UDP). This
# machine (the run's) is the map-resolver: a mapping host on $HOST_LISTEN:4342 (its own region file; not an area host, so
# it does not announce itself), the site for the name, and the area host's registration (its own TTL), over the session.
if [ -n "${MAP_AREA:-}" ]; then
    RINA_HOST_ROLE=mapping RINA_REGION_FILE=$R/mapping.perm unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec rina-host --listen $HOST_LISTEN:4342 --api $API" > $R/mapping-host.log 2>&1 &
    for i in $(seq 100); do (exec 3<>/dev/tcp/$HOST_LISTEN/4342) 2>/dev/null && break; sleep 0.05; done
    rina-lisp map-site "$MAP_AREA" "${MAP_KEY:?MAP_KEY}" "$HOST_LISTEN" || die "no mapping region on $HOST_LISTEN:4342"
    rina-lisp map-register "$MAP_AREA" "$HOST_LISTEN" "$HOST_LISTEN" "$MAP_KEY" 86400 || die "the host could not register its area in the mapping region"
    export LISP_AREA=$MAP_AREA                                    # the run's own agent is told the area's name (as in the LISP runs)
fi

# ---- 4/5. the tests --------------------------------------------------------------------------------------------------
printf "n\ttest\tresult\tseconds\thost_fds\n" > "$R/suite.tsv"
cd "$ROOT"
n=0
# TESTS='/path/a.sh /path/b.sh' runs those instead (the FD&F experiments: experiments/*.sh), exactly as rlite's own tests run
for t in ${TESTS:-tests/integration/*.sh}; do
    n=$((n + 1)); [ $n -lt "$FIRST" ] && continue; [ $n -gt "$LAST" ] && break
    name=$(basename "$t" .sh)
    kill -0 "$HOSTPID" 2>/dev/null || { printf "%d\t%s\tHOST_GONE\t0\n" $n "$name" >> "$R/suite.tsv"; continue; }
    # [AGENT_LOGS_KEPT_V1] every agent's stderr (daemonized) goes to $R/agent.<machine>.log, so a refusal is visible
    export RINA_AGENT_LOG=$R
    # [MR_IS_A_CAPABILITY_V1] the run's own agent sits where the host (and, in the LISP runs, the mapping system) is; in
    # the LISP runs it is told the area's NAME, which makes it the area's first member: it probes itself for the
    # map-resolver role and answers its links with name:port@<elected resolver>
    # [AREA_HOST_REGISTERS_ITSELF_V1] in the mapping runs the run's agent is given the NAME only (no RINA_RAM): it reaches
    # the area through the mapping, as every other member, so a host that moves is found where it registered
    if [ -n "${MAP_AREA:-}" ]; then env -u RINA_RAM RINA_AREA=$MAP_AREA RINA_MAP_KEY=$MAP_KEY rlite-uipcps -d
    elif [ -n "${LISP_AREA:-}" ]; then RINA_AREA=$LISP_AREA rlite-uipcps -d; else rlite-uipcps -d; fi || { printf "%d\t%s\tAGENT_FAILED\t0\n" $n "$name" >> "$R/suite.tsv"; continue; }
    s=$(date +%s.%N)
    # the test runs in the foreground, exactly as rlite runs it; a watchdog beside it dumps the host's threads once,
    # if the test is still running after HANG_S seconds, and is gone when the test ends
    ( sleep "$HANG_S"; echo "run.sh: $name still running after ${HANG_S}s: dumping the host's threads -> $R/$name.hostdump"
      gdb -p "$HOSTPID" -batch -ex "thread apply all bt 14" 2>&1 | grep -v "^\[New" > "$R/$name.hostdump" ) & WD=$!
    timeout "$TEST_S" "$t" > "$R/$name.log" 2>&1; rc=$?; e=$(date +%s.%N)
    kill $WD 2>/dev/null; wait $WD 2>/dev/null
    # [HOST_RESTARTS_IN_A_RUN_V1] an experiment may kill the host and start another (5b): the run goes on with that one
    if ! kill -0 "$HOSTPID" 2>/dev/null && NEW=$(pgrep -nx rina-host); then say "the host is now pid $NEW (started by $name)"; HOSTPID=$NEW; fi
    rlite-ctl reset > /dev/null; rlite-ctl terminate > /dev/null; rlite-ctl reset > /dev/null
    for p in rinaperf rina-echo-async iporinad; do pkill -x $p; done
    for i in $(seq 50); do pgrep -x rlite-uipcps >/dev/null || break; sleep 0.1; done
    if [ $rc -eq 0 ]; then r=PASS; else r="FAIL(rc=$rc)"; fi
    fds=$(ls /proc/$HOSTPID/fd 2>/dev/null | wc -l)        # open descriptors in the host after the test: must not grow
    printf "%d\t%s\t%s\t%.2f\t%s\n" $n "$name" "$r" "$(echo "$e - $s" | bc)" "$fds" >> "$R/suite.tsv"
    printf "  %-34s %-14s host fds %s\n" "$name" "$r" "$fds"
done

# ---- 6. report -------------------------------------------------------------------------------------------------------
echo; awk -F'\t' 'NR>1{print $3}' "$R/suite.tsv" | sort | uniq -c
for f in $(awk -F'\t' 'NR>1 && $3!="PASS"{print $2}' "$R/suite.tsv"); do
    echo; echo "==== $f: end of its log"; tail -15 "$R/$f.log" 2>/dev/null
    [ -s "$R/$f.hostdump" ] && { echo "==== $f: the host's threads while it ran (full: $R/$f.hostdump)"; grep -E "^Thread|^#[0-3] " "$R/$f.hostdump" | head -60; }
done
kill -0 "$HOSTPID" 2>/dev/null && say "host still running at the end" || { say "THE HOST IS GONE; end of its log:"; tail -15 "$R/host.log"; }
say "everything from this run: $R"
