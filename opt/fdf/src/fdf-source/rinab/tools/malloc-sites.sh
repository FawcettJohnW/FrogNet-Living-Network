#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# malloc-sites.sh [N] -- [ALLOC_ONLY_WHEN_NEEDED_V1] how many mallocs the host makes per rr transaction, and where:
# links the built host objects with -rdynamic (so call sites have names), preloads tools/mcount.so (samples 1 malloc in
# 64 with its call stack), runs rinaperf rr N, prints the total per transaction and the top call sites.
N=${1:-2000}; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build; O=/tmp/malloc-sites; mkdir -p $O
gcc -O2 -shared -fPIC $H/tools/mcount.c -o $O/mcount.so -ldl || exit 2
g++ -O2 -pthread -rdynamic -o $O/rina-host $B/obj/ram_server_rina.o $B/obj/rina_region.o $B/obj/ctl_host.o $B/obj/frogram.o -llz4 -lcrypto || exit 2
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:8876 RINA_RAM_NETNS=/proc/$$/ns/net RIBBIT_TEMPLATES=$H/templates/rina.prime
MCOUNT_OUT=$O/host.txt LD_PRELOAD=$O/mcount.so $O/rina-host --listen 127.0.0.1:8876 --api /FawcettInnovations.RINA.ram_interface.php > $O/host.log 2>&1 & HP=$!
sleep 0.5; rlite-uipcps -d 2>/dev/null; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv 2>/dev/null; sleep 0.3
rinaperf -z srv -t rr -c $N 2>&1 | grep Sender
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 0.5; kill -TERM $HP; sleep 1; pkill -x rlite-uipcps
awk -v n=$N 'NR==1{printf "host mallocs: %s = %.0f per transaction\n", $2, $2/n}' $O/host.txt
sed -n 2,30p $O/host.txt | c++filt | sed 's/std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >/str/g' | cut -c1-200
