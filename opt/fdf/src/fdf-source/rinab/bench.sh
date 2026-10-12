#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# bench.sh RLITE_ROOT [ROUNDS] -- rlite (its kernel modules + rlite-uipcps) against the RINA region (no modules, no
# daemon), on THIS machine, in one command, as root. Same topology for both, rlite's own (test 0024 / VM scenario 0027):
# namespaces green and red on a veth pair; a normal DIF "mydif" over shim-eth "edif"; red enrolled through green;
# rinaperf server in green, client in red. The same rinaperf source, built against each. Per round:
#   ping 2000 x 64 B back to back | rr 20000 x 64 B | perf 20000 x 1400 B | 16 parallel pings x 500
# Then lines of code for each (cloc if installed, else non-blank lines).
#
# rlite is built from a COPY of RLITE_ROOT under ./bench-build (your tree is not touched): ./configure --opt, its
# user programs, and its kernel modules against the running kernel. One change in the copy: rlite's CMakeLists turns
# every warning into an error (-Werror), and this compiler warns on rlite-ctl.c (format-truncation); the copy builds
# without -Werror. The modules are loaded for the rlite arm and unloaded after it.
# ONLY_REGION=1 runs the region arm alone (no rlite build, no modules): for checking this script where modules can't load.
set -u
ONLY_REGION=${ONLY_REGION:-0}
ROOT=${1:?usage: ./bench.sh RLITE_ROOT [ROUNDS]}; ROUNDS=${2:-3}; PORT=${PORT:-8840}
H=$(cd "$(dirname "$0")" && pwd); R=$H/bench-runs/$(date +%Y%m%d-%H%M%S); mkdir -p "$R"; OUT=$R/raw.txt
say() { echo "bench.sh: $*"; }
die() { echo "bench.sh: STOP: $*" >&2; exit 2; }

[ "$(id -u)" = 0 ] || die "run as root"
ROOT=$(cd "$ROOT" 2>/dev/null && pwd) || die "no directory $1"
[ -d "$ROOT/kernel" ] && [ -x "$ROOT/configure" ] || die "$ROOT is not the rlite source tree"
for t in g++ gcc cmake protoc make ip; do command -v $t >/dev/null || die "missing command: $t"; done
[ "$ONLY_REGION" = 1 ] || [ -d "/lib/modules/$(uname -r)/build" ] || die "no kernel headers for $(uname -r): apt install linux-headers-$(uname -r)"
for p in rina-host rlite-uipcps rinaperf; do pgrep -x "$p" >/dev/null && die "a $p is already running -- stop it first"; done
grep -q "^rlite" /proc/modules && die "rlite modules are already loaded (rmmod rlite-normal rlite-shim-eth rlite-shim-loopback rlite)"
[ -z "$(ip netns list)" ] || die "network namespaces already exist: $(ip netns list | awk '{print $1}' | tr '\n' ' ')"

# ---- build both ----------------------------------------------------------------------------------------------------
B=$H/bench-build; RL=$B/rlite; mkdir -p "$B"
say "building the RINA region (log $R/build-region.log)"
{ "$H/tools/build.sh" && "$H/tools/build_rlite.sh" "$ROOT"; } > "$R/build-region.log" 2>&1 || { tail -20 "$R/build-region.log"; die "region build failed"; }
if [ "$ONLY_REGION" != 1 ]; then
say "building rlite from a copy of $ROOT (log $R/build-rlite.log)"
rm -rf "$RL" && cp -a "$ROOT" "$RL" && sed -i 's/add_definitions("-Wall -g -Werror")/add_definitions("-Wall -g")/' "$RL/CMakeLists.txt"
( cd "$RL" && ./configure --opt --no-swig && make usr && make ker ) > "$R/build-rlite.log" 2>&1 || { tail -25 "$R/build-rlite.log"; die "rlite build failed (kernel $(uname -r))"; }
for f in kernel/rlite.ko kernel/rlite-normal.ko kernel/rlite-shim-eth.ko build/user/uipcps/rlite-uipcps build/user/tools/rinaperf build/user/tools/rlite-ctl; do
    [ -e "$RL/$f" ] || die "rlite build produced no $f"; done
mkdir -p "$B/rlite-bin"; ln -sf "$RL/build/user/uipcps/rlite-uipcps" "$RL/build/user/tools/rinaperf" "$RL/build/user/tools/rlite-ctl" "$B/rlite-bin/"
fi

# ---- the topology and the measurement, the same for both -------------------------------------------------------------
topo_up() {
    ip link add veth.green type veth peer name veth.red
    ip netns add green; ip netns add red
    ip link set veth.green netns green; ip link set veth.red netns red
    for n in green red; do ip netns exec $n ip link set lo up; ip netns exec $n ip link set veth.$n up; done
}
topo_down() { ip netns delete green 2>/dev/null; ip netns delete red 2>/dev/null; ip link del veth.green 2>/dev/null; }
setup() {
    for n in green red; do ip netns exec $n rlite-uipcps -d || die "$1: rlite-uipcps -d failed in $n"; done
    ip netns exec green rlite-ctl ipcp-create green.eth shim-eth edif >/dev/null && ip netns exec green rlite-ctl ipcp-config green.eth netdev veth.green &&
    ip netns exec green rlite-ctl ipcp-create green.n normal mydif >/dev/null && ip netns exec green rlite-ctl ipcp-enroller-enable green.n &&
    ip netns exec green rlite-ctl ipcp-register green.n edif &&
    ip netns exec red rlite-ctl ipcp-create red.eth shim-eth edif >/dev/null && ip netns exec red rlite-ctl ipcp-config red.eth netdev veth.red &&
    ip netns exec red rlite-ctl ipcp-create red.n normal mydif >/dev/null && ip netns exec red rlite-ctl ipcp-register red.n edif &&
    ip netns exec red rlite-ctl ipcp-enroll red.n mydif edif green.n || die "$1: setup failed (see above)"
}
measure() {
    ip netns exec green rinaperf -lw -z rpsrv || die "$1: rinaperf server did not register"
    sleep 1
    for round in $(seq "$ROUNDS"); do
        say "$1 round $round"
        echo "CASE arm=$1 round=$round test=ping" >> "$OUT"; ip netns exec red rinaperf -z rpsrv -t ping -c 2000 -s 64 -i 0 >> "$OUT" 2>&1 || echo "CASE_FAIL $1 ping rc=$?" >> "$OUT"
        echo "CASE arm=$1 round=$round test=rr"   >> "$OUT"; ip netns exec red rinaperf -z rpsrv -t rr -c 20000 -s 64 >> "$OUT" 2>&1 || echo "CASE_FAIL $1 rr rc=$?" >> "$OUT"
        echo "CASE arm=$1 round=$round test=perf" >> "$OUT"; ip netns exec red rinaperf -z rpsrv -t perf -c 20000 -s 1400 >> "$OUT" 2>&1 || echo "CASE_FAIL $1 perf rc=$?" >> "$OUT"
        echo "CASE arm=$1 round=$round test=p16"  >> "$OUT"; ip netns exec red rinaperf -z rpsrv -t ping -p 16 -c 500 -s 64 -i 0 >> "$OUT" 2>&1 || echo "CASE_FAIL $1 p16 rc=$?" >> "$OUT"
    done
    for n in green red; do ip netns exec $n rlite-ctl reset >/dev/null 2>&1; ip netns exec $n rlite-ctl terminate >/dev/null 2>&1; done
    pkill -x rinaperf; sleep 1; pkill -x rlite-uipcps
}

# ---- arm 1: rlite ----------------------------------------------------------------------------------------------------
unload() { for m in rlite-normal rlite-shim-eth rlite-shim-loopback rlite; do rmmod $m 2>/dev/null; done; }
trap 'pkill -x rinaperf; pkill -x rlite-uipcps; pkill -x rina-host; topo_down; unload' EXIT
if [ "$ONLY_REGION" != 1 ]; then
say "rlite: loading its modules"
for m in rlite rlite-shim-loopback rlite-shim-eth rlite-normal; do insmod "$RL/kernel/$m.ko" || die "insmod $m.ko failed"; done
topo_up
( export PATH=$B/rlite-bin:$PATH; setup rlite; measure rlite )
topo_down; unload; say "rlite: modules unloaded"
fi

# ---- arm 2: the RINA region ------------------------------------------------------------------------------------------
# [PRIMED_TEMPLATES_V1] both ends learn the primed store at start (PRIME=0 to learn on the wire instead)
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")" && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")" && pwd)/templates/rina.prime
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime
export PATH=$H/build/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
"$H/build/bin/rina-host" --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > "$R/host.log" 2>&1 &
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.05; done
pgrep -x rina-host >/dev/null || die "the region host did not start"
topo_up
( setup region; measure region )
topo_down; pkill -x rina-host

# ---- results -------------------------------------------------------------------------------------------------------
python3 - "$OUT" > "$R/RESULTS.md" <<'PY'
import re, sys, statistics as st
arm = test = None; d = {}
for l in open(sys.argv[1]):
    m = re.match(r'CASE arm=(\w+) round=\d+ test=(\w+)', l)
    if m: arm, test = m.groups(); continue
    if l.startswith('CASE_FAIL'): d.setdefault((l.split()[1], l.split()[2], 'fail'), []).append(1); continue
    if arm is None: continue
    m = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/', l)
    if m: d.setdefault((arm, test, 'avg_ms'), []).append(float(m.group(2)))
    m = re.match(r'\s*Sender\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)', l)
    if m and test == 'rr': d.setdefault((arm, 'rr', 'ktps'), []).append(float(m.group(2))); d.setdefault((arm, 'rr', 'lat_us'), []).append(int(m.group(4)) / 1000)
    m = re.match(r'\s*Receiver\s+(\d+)\s+([\d.]+)\s+([\d.]+)', l)
    if m and test == 'perf': d.setdefault((arm, 'perf', 'rx'), []).append(int(m.group(1))); d.setdefault((arm, 'perf', 'mbps'), []).append(float(m.group(3)))
def med(arm, t, k):
    v = d.get((arm, t, k)); return f"{st.median(v):.3f}" if v else "—"
rows = [('ping avg rtt (ms, median of rounds)', 'ping', 'avg_ms'), ('rr (k transactions/s)', 'rr', 'ktps'), ('rr latency (µs)', 'rr', 'lat_us'),
        ('perf received (Mbps)', 'perf', 'mbps'), ('perf SDUs received (of 20000)', 'perf', 'rx'), ('16 parallel pings, avg rtt (ms)', 'p16', 'avg_ms')]
print("| measure | rlite | RINA region |\n|---|---:|---:|")
for name, t, k in rows: print(f"| {name} | {med('rlite', t, k)} | {med('region', t, k)} |")
fails = [k for k in d if k[2] == 'fail']
print("\nfailed cases: " + (", ".join(f"{a} {t} x{len(d[(a, t, 'fail')])}" for a, t, _ in fails) if fails else "none"))
PY
{
echo; echo "## Lines of code"
if command -v cloc >/dev/null; then
    echo '```'; echo "rlite: kernel + user/uipcps + user/libs + rlite-ctl"
    cloc --quiet --hide-rate "$ROOT/kernel" "$ROOT/user/uipcps" "$ROOT/user/libs" "$ROOT/user/tools/rlite-ctl.c" | tail -n +2
    echo "RINA region: region + host glue + librina + agent + rlite-ctl (client and its ported command logic)"
    cloc --quiet --hide-rate "$H/src" "$H/tools/apply_session_hook.py" "$H/tools/make_host.sh" | tail -n +2; echo '```'
else
    nb() { cat "$@" | grep -cv '^\s*$'; }
    echo "non-blank lines (install cloc for code-only counts):"
    echo "- rlite: $(nb $(find "$ROOT/kernel" "$ROOT/user/uipcps" "$ROOT/user/libs" -name '*.[ch]' -o -name '*.cpp' -o -name '*.hpp' | grep -v /build/) "$ROOT/user/tools/rlite-ctl.c")"
    echo "- RINA region: $(nb $(find "$H/src" -name '*.cpp' -o -name '*.hpp' -o -name '*.h') "$H/tools/apply_session_hook.py" "$H/tools/make_host.sh")"
fi
} >> "$R/RESULTS.md"
echo; cat "$R/RESULTS.md"; echo; say "raw output and logs: $R"
