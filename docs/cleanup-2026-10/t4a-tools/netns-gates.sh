L=${L:-/home/claude/clean/docs/cleanup-2026-10/t4a-logs}
cd /home/claude/clean/opt/fdf/src/fdf-source/rinab
# dockerd on this host (the containers) loads br_netfilter and sets FORWARD to DROP, so frames between the harness's
# namespaces over its own bridges hit DROP. While the harness runs, bridged frames are accepted (DOCKER-USER), then
# the rule is removed. [sandbox only]
iptables -I DOCKER-USER -m physdev --physdev-is-bridged -j ACCEPT
trap 'iptables -D DOCKER-USER -m physdev --physdev-is-bridged -j ACCEPT' EXIT
timeout 900 bash tools/netns-region-loop-test.sh > $L/region.txt 2>&1; echo "EXIT $?" >> $L/region.txt
NOKEY=a timeout 900 bash tools/netns-region-loop-test.sh > $L/nokey.txt 2>&1; echo "EXIT $?" >> $L/nokey.txt
timeout 900 bash tools/netns-lan-only-test.sh > $L/lanonly.txt 2>&1; echo "EXIT $?" >> $L/lanonly.txt
timeout 900 bash tools/netns-ponds-daemon-test.sh > $L/ponds.txt 2>&1; echo "EXIT $?" >> $L/ponds.txt
BRIDGE_KT=/home/claude/frognet-android/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt FDF_TUN=/home/claude/frognet-android/tun/out/fdf-tun-x86_64 timeout 1500 bash tools/netns-reach-test.sh > $L/reach.txt 2>&1; echo "EXIT $?" >> $L/reach.txt
echo done > $L/netns-gates.done
