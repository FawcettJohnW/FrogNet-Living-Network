# fdf-hub: the region's WireGuard concentrator (PLAN-FROGNET-ON-FDF Stage 1)

One wg interface per region (`wg-<area>`, 10.253.253.254/16), one peer per live member, prefixes only from verified
mappings naming this hub. Nothing polls: `fdf-events` holds one session and reports changes; each is one reconcile.

## On the droplet (Ubuntu 22.04, as root)

    cd / && tar xvzf - < /tmp/fdf-node.tgz; cd / && tar xvzf - < /tmp/fdf-region-update.tgz
    apt-get install -y wireguard-tools            # wg, wg-quick (kernel module is in 22.04's kernel)
    # the region's site key (every node that registers mappings signs with it; set it ONCE in the region):
    curl -s -X POST -d '{"op":"map_site","name":"frognet","key":"<site key>"}' \
         "http://127.0.0.1:8840/FawcettInnovations.RINA.ram_interface.php?op=rina"
    # the hub's own membership: fdf-client with the hub's facts (fdf-hub writes /opt/fdf/var/facts.json)
    echo 'RINA_RAM=127.0.0.1:8840' > /opt/fdf/var/join.env
    systemctl daemon-reload && systemctl enable --now fdf-client fdf-hub
    journalctl -u fdf-hub -f                       # "[FDF-HUB] start (adopt): members=.. peers=.."
    ufw allow 50820/udp   # outside the broker's UDP range 51000-59999

`fdf-ls` on any member shows the hub as a participant (role hub). `wg show wg-frognet` shows the peers.
Oracle (no wg needed): `python3 /opt/fdf/hub/hub_oracle.py` (15 checks, with controls).

## On a FrogNet node (region mode of the tunnel daemon)

    cd / && tar xvzf - < /tmp/fdf-node.tgz; cd / && tar xvzf - < /tmp/fdf-region-update.tgz            # (+ fdf-arm64.tgz on a Pi)
    cd / && tar xvzf - < /tmp/frognet-region-patch.tgz # discovery + internet_tunnels_v3 changes
    echo 'RINA_RAM=165.232.153.144:8840' > /opt/fdf/var/join.env
    printf 'RINA_AREA=frognet\nRINA_SITE_KEY=<site key>\n' > /opt/fdf/var/site.env
    systemctl daemon-reload && systemctl enable --now fdf-client
    # tunnel daemon on the region: add to /etc/frognet/tunnel.conf
    #   RINA_RAM=165.232.153.144:8840
    #   RINA_AREA=frognet
    # discovery gather from the region: export FROGNET_DISCOVERY_SOURCE=region (where runMerge's environment is set)
    systemctl restart frognet-tunnel-daemon-v3
    cat /var/lib/frognet/view.json                   # phase, gen, dbhost, hosts_hash

Facts file: /opt/fdf/var/facts.json is written by the daemon (register, transit, maps); fdf-client announces it
and registers facts.maps as signed, session-bound mappings. Never edit the region directly.
