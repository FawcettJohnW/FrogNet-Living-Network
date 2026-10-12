echo "fnid: $(cat /etc/fnid)"
echo "opts_only: $(grep -E "^domain=|router" /etc/dnsmasq.d/opts_only.conf | tr "\n" " ")"
echo "eth1: $(ip -o -4 addr show eth1 | awk '{print $4}' | tr "\n" " ")"
echo "key md5: $(md5sum < /var/lib/frognet-tunnel/node_private.key)"
echo "tunnel.conf: $(grep -v "^#" /etc/frognet/tunnel.conf 2>/dev/null | tr "\n" " ")"
for f in /etc/frognet/broker.conf /etc/frognet/pond.conf /etc/frognet/tunnel.conf.bak.* /etc/frognet/tunnel.conf.20* /etc/frognet/pond_bootstrap_done /etc/systemd/system/frognet-tunnel-setup.service /etc/systemd/system/frognet-pond-bootstrap.timer /etc/systemd/system/frognet-pond-bootstrap.service /usr/local/bin/frognet-tunnel-setup-v3.sh /usr/local/bin/frognet-pond-bootstrap.sh /opt/frognet_semantic/broker /etc/NetworkManager/dispatcher.d/91-frognet-connectivity /etc/NetworkManager/dispatcher.d/99-ifup /etc/systemd/system/frognet-broker-v4.service /var/lib/frognet-tunnel/last_broker_state.json; do [ -e "$f" ] && echo "present: $f"; done
echo "90-frognet-merge lines: $(wc -l < /etc/NetworkManager/dispatcher.d/90-frognet-merge)"
