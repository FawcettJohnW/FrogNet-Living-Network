#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# loop-test.sh RLITE_ROOT TEST N -- one rlite test N times against ONE host (as run.sh starts it, isolated), resetting
# between runs (WRAP=DIR puts DIR first on PATH, e.g. to trace one program); on the first failure prints the test's log tail and what the region holds (peek), and stops.
set -u
ulimit -c unlimited
ROOT=$1; T=$2; N=${3:-30}; PORT=${PORT:-8853}; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build
export RINA_EXIT_TRACE=${RINA_EXIT_TRACE:-}
export PATH=${WRAP:+$WRAP:}$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
mkdir -p /var/run/netns
unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec $B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php" > /tmp/lt-host.log 2>&1 &
trap 'pkill -x rinaperf; pkill -x rlite-uipcps; pkill -x rina-host' EXIT
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.05; done
cd "$ROOT"
for i in $(seq "$N"); do
  rlite-uipcps -d
  if ! timeout 60 tests/integration/$T.sh > /tmp/lt-test.log 2>&1; then
    echo "run $i FAILED"; tail -5 /tmp/lt-test.log
    for v in sess fl app ibh proc; do echo "--- $v"; $B/bin/peek $v | tail -12; done
    for p in $($B/bin/peek sess | grep -o "proc:[^ ]*" | sed "s/proc://" | sort -u); do echo "--- ev:$p"; $B/bin/peek "ev:$p" | tail -6; done
    for pid in $(pgrep -x rinaperf); do echo "--- rinaperf pid $pid: tasks (tid wchan syscall-number)"; for t in /proc/$pid/task/*; do echo "  $(basename $t) $(cat $t/wchan 2>/dev/null) $(cut -d' ' -f1 $t/syscall 2>/dev/null)"; done
      echo "  state/utime/stime/exit_code: $(awk '{print $3, $14, $15, $52}' /proc/$pid/stat)  ppid: $(awk '{print $4}' /proc/$pid/stat)  cmd: $(tr '\0' ' ' < /proc/$pid/cmdline)"; sleep 1; echo "  1 s later utime/stime: $(awk '{print $14, $15}' /proc/$pid/stat)"
      echo "  its TCP connections: $(ss -tnp 2>/dev/null | grep "pid=$pid," | awk '{print $4"->"$5}' | tr '\n' ' ')"; done
    for pid in $(pgrep -x rinaperf); do echo "--- rinaperf pid $pid threads"; gdb -p $pid -batch -ex "thread apply all bt 10" 2>&1 | grep -vE "^\[New|Download|^warning: (Target|could not)" | cut -c1-170 | head -80; done
    exit 1
  fi
  rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; rlite-ctl reset >/dev/null 2>&1
  pkill -x rinaperf; for k in $(seq 50); do pgrep -x rlite-uipcps >/dev/null || break; sleep 0.1; done
done
echo "$N runs, no failure"
