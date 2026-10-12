# DEPLOY: the first region on the existing FrogNet (2026-10-09, revised: RAM manager + hub on the droplet)

## Current install (2026-10-11, build fdf-2026-10-11-r19-a15): read this first

The sections below are the 10-09 deployment as it was done (separate tarballs, units written by hand). The files are
now one per machine type, in the repo's opt/fdf/dist, and the droplet's three units ship in fdf-droplet.tgz.

The droplet, the first time:

    cd / && tar xvzf - < /tmp/fdf-droplet.tgz          # /opt/fdf + fdf-ram-frognet, fdf-client-frognet, fdf-hub units
    apt-get install -y wireguard-tools
    systemctl daemon-reload && systemctl enable --now fdf-ram-frognet
    curl -s -X POST -d '{"op":"map_site","name":"frognet","key":"<SITE KEY>"}' \
      "http://127.0.0.1:51888/FawcettInnovations.RINA.ram_interface.php?op=rina"       # -> {"ok":true}
    cat > /opt/fdf/hub/hub.env <<'EOF2'
    RINA_RAM=127.0.0.1:51888
    FDF_AREA=frognet
    FDF_HUB_NAME=StreamingFrog
    FDF_ENDPOINT=165.232.153.144:50889
    FDF_LISTEN=50889
    FDF_FACTS=/opt/fdf/var/facts-frognet.json
    FDF_BIN=/opt/fdf/bin
    EOF2
    systemctl enable --now fdf-client-frognet fdf-hub
    ufw allow 51888/tcp
    ufw allow 50889/udp
    ufw route allow in on wg-frognet out on wg-frognet

(fdf-hub.service passes `--facts ${FDF_FACTS}`: hub.env must name it, the same file fdf-client-frognet announces.
The tarball never carries hub.env.)

The droplet, every later build:

    cd / && tar xvzf - < /tmp/fdf-droplet.tgz && systemctl daemon-reload && systemctl restart fdf-ram-frognet fdf-client-frognet fdf-hub

A node (Pi or x86), first time and every build:

    sudo bash /tmp/fdf-install-aarch64.run <SITE KEY>      # x86: fdf-install-x86_64.run

It unpacks /opt/fdf and the FrogNet region patch, runs /opt/fdf/bin/fdf-region-join (site.env, join.env,
fdf-client, the tunnel daemon restarted in region mode) and shows the result for 15 s.

A Windows machine: the bridge, repo opt/fdf/windows/bridge (README.txt), in place of section 3. A phone: the FrogNet
member app, repo opt/fdf/src/frognet-android. Neither types an address: the region gives a leaf its /32.

Machines:

| Machine | Role | What it gets |
|---|---|---|
| StreamingFrog, 165.232.153.144 (the droplet) | RAM manager (`rina-host` on TCP **51888**) + hub (`fdf-hub`, UDP **50889**), NEXT TO broker v4, which keeps running | fdf-region-update.tgz over the fdf-node.tgz already there (or the complete fdf-node.tgz; same files) |
| 10.170.170.1 | node | fdf-node.tgz (complete, 2026-10-09: everything, nothing else needed on x86), fdf-arm64.tgz on top if `uname -m` = aarch64, frognet-region-patch.tgz |
| 10.251.251.1 | node | the same |
| this laptop (Windows) | leaf | fdf-windows-leaf.zip: `fdf-client.exe` + `facts.json`, WireGuard for Windows |
| 10.250.250.100 (AI-Host) | leaf, later | fdf-node.tgz, section 4 |

Every machine reaches the droplet's public address, so the "reachable without the broker's tunnels" condition is
met by construction. Nothing below touches the LAN side of FrogNet; a node not flipped keeps running the old code.

## Not interfering with broker v4 (checked against its source, frognet_broker_v4.py / broker_namespace_v4.py)

| Broker v4 uses | The region uses | Clash? |
|---|---|---|
| TCP 18428 (uvicorn, loopback), 8443 (API) | TCP 51888 (rina-host) | no |
| UDP 51000-59999 (`wgports.conf`), DNAT per port | UDP 50889 (hub's wg) | no: outside the range |
| wg interfaces `wg_<hash>_h<n>/_j<n>` INSIDE a netns per pond | `wg-frognet` in the main namespace | no |
| 10.253.200+.x /30 transit INSIDE the pond netns; 10.252.254.0/24 veths in the main namespace | 10.253.253.254/16 on wg-frognet (main ns); edges 10.253.A.B; leaves 10.252.0.x | no, PROVIDED nothing in the main namespace already carries 10.253/16: check below. Leaves must avoid 10.252.254.0/24 |
| MASQUERADE + FORWARD inside its netns | ip_forward + FORWARD between wg-frognet peers in the main namespace | no; but ufw's forward policy must allow it (below) |
| sqlite in /var/lib/frognet_broker_v4 | /opt/fdf/var/area-frognet.perm | no |
| the droplet's existing FD&F demo region on 8840 (fdf_service) and its fdf-client (join.env -> 8840) | a SECOND rina-host on 51888 with its own region file, and a SECOND fdf-client unit with its own facts file | no: separate units, separate files |

Before starting, on the droplet, both must print nothing:

    ip -o addr | grep '10\.253\.'
    ip route | grep '10\.253\.'

If either prints a line (wg-bb / wg-ib carrying 10.253), stop and say what it printed; the hub's address block moves.

## 0. Once: the site key

    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; echo

Keep it; it goes on every node and leaf as `RINA_SITE_KEY`.

## 1. The droplet: RAM manager on 51888, hub on 50889, broker untouched

    cd / && tar xvzf - < /tmp/fdf-region-update.tgz
    apt-get install -y wireguard-tools          # 22.04's kernel has WireGuard; if `ip link add x type wireguard` fails: apt-get install wireguard-go, and FDF_WG_USERSPACE=1 in hub.env

The region's RAM manager, its own instance and file (the demo region on 8840 keeps running):

    cat > /etc/systemd/system/fdf-ram-frognet.service <<'EOF'
    [Unit]
    Description=FD&F RAM manager (rina-host) for the frognet region, port 51888
    After=network-online.target
    [Service]
    Environment=RINA_REGION_FILE=/opt/fdf/var/area-frognet.perm
    Environment=RIBBIT_TEMPLATES=/opt/fdf/templates/fdf.prime
    ExecStart=/opt/fdf/bin/rina-host --listen 0.0.0.0:51888 --api /FawcettInnovations.RINA.ram_interface.php
    Restart=always
    RestartSec=1
    [Install]
    WantedBy=multi-user.target
    EOF
    systemctl daemon-reload && systemctl enable --now fdf-ram-frognet
    curl -s -X POST -d '{"op":"map_site","name":"frognet","key":"<SITE KEY>"}' \
      "http://127.0.0.1:51888/FawcettInnovations.RINA.ram_interface.php?op=rina"       # -> {"ok":true}

The hub's own membership: a second fdf-client unit (the existing one, if any, stays on 8840):

    cat > /etc/systemd/system/fdf-client-frognet.service <<'EOF'
    [Unit]
    Description=FD&F member session of the droplet in the frognet region (the hub's membership)
    After=fdf-ram-frognet.service
    [Service]
    Environment=RINA_RAM=127.0.0.1:51888
    Environment=RIBBIT_TEMPLATES=/opt/fdf/templates/fdf.prime
    Environment=FDF_FACTS=/opt/fdf/var/facts-frognet.json
    Environment=FDF_NAME=StreamingFrog
    ExecStart=/opt/fdf/bin/fdf-client
    Restart=always
    RestartSec=1
    [Install]
    WantedBy=multi-user.target
    EOF

The hub:

    cat > /opt/fdf/hub/hub.env <<'EOF'
    RINA_RAM=127.0.0.1:51888
    FDF_AREA=frognet
    FDF_HUB_NAME=StreamingFrog
    FDF_ENDPOINT=165.232.153.144:50889
    FDF_LISTEN=50889
    FDF_FACTS=/opt/fdf/var/facts-frognet.json
    FDF_BIN=/opt/fdf/bin
    EOF
    sed -i 's|--listen ${FDF_LISTEN}|--listen ${FDF_LISTEN} --facts /opt/fdf/var/facts-frognet.json|; s|After=network-online.target fdf-client.service|After=network-online.target fdf-client-frognet.service|' /etc/systemd/system/fdf-hub.service
    systemctl daemon-reload && systemctl enable --now fdf-client-frognet fdf-hub
    journalctl -u fdf-hub -n 5 --no-pager        # "[FDF-HUB] hub StreamingFrog on wg-frognet ... start (adopt): members=1 peers=0"
    ufw allow 51888/tcp
    ufw allow 50889/udp
    ufw route allow in on wg-frognet out on wg-frognet     # member <-> member forwarding through the hub (ufw's forward policy is DROP)

Check that broker v4 is unaffected: `systemctl status frognet-broker-v4` unchanged, `wg show` in its namespaces
unchanged (`ip netns list`; `ip netns exec <ns> wg show`), and a broker-mode node still handshakes.

## 2. 10.170.170.1 and 10.251.251.1: nodes

    cd / && tar xvzf - < /tmp/fdf-node.tgz
    uname -m | grep -q aarch64 && cd / && tar xvzf - < /tmp/fdf-arm64.tgz
    cd / && tar xvzf - < /tmp/frognet-region-patch.tgz
    cp /etc/frognet/tunnel.conf /etc/frognet/tunnel.conf.broker       # the way back
    printf 'RINA_RAM=165.232.153.144:51888\nRINA_AREA=frognet\n' >> /etc/frognet/tunnel.conf
    printf 'RINA_AREA=frognet\nRINA_SITE_KEY=<SITE KEY>\n' > /opt/fdf/var/site.env
    fdf-join 165.232.153.144:51888                 # writes join.env, installs + starts fdf-client.service (facts, site.env)
    systemctl restart frognet-tunnel-daemon-v3
    journalctl -u frognet-tunnel-daemon-v3 -n 20 --no-pager
      # "Region mode: RINA_RAM=165.232.153.144:51888", "Starting region loop", then runMerge
    wg show                                        # one tunnel, peer = the hub, endpoint 165.232.153.144:50889
    cat /opt/fdf/var/facts.json                    # role node, subnet, pubkey, maps:[{eid: its /24, rlocs: "StreamingFrog:1:50"}]
    cat /var/lib/frognet/view.json                 # phase steady, gen, dbhost, hosts_hash
    fdf-ls                                         # the members, this node among them with its facts

`RINA_RAM=` in tunnel.conf is the ONE switch: the daemon answers the broker's four calls from the region,
discovery.live gathers from the region (it reads the same line), and propogateNotificationInternal does nothing
(the facts write is the propagation). To go back: `cp /etc/frognet/tunnel.conf.broker /etc/frognet/tunnel.conf`,
`fdf-leave`, restart the daemon.

On the hub, after each node:

    journalctl -u fdf-hub -f            # "event p: members=N peers=.." then "event m: ... prefixes=.."
    wg show wg-frognet                  # the node's pubkey, allowed-ips 10.253.A.B/32,10.A.B.0/24, latest handshake
    ip route show dev wg-frognet        # 10.A.B.0/24 via 10.253.A.B

From 10.170.170.1: `ping 10.251.251.1`, `ssh FrogNetHost.<name of 10.251.251.1>`.

## 3. The laptop (Windows): a leaf

Unzip fdf-windows-leaf.zip somewhere (say `C:\fdf`). Install WireGuard for Windows (wireguard.com/install).

facts.json next to fdf-client.exe (edit name; make the key pair in the WireGuard app: "Add empty tunnel" shows
the public key):

    {"role":"leaf","name":"Laptop","guid":"laptop-1","pubkey":"<the laptop's wg public key>"}

    (no addr: the RAM manager gives the leaf its /32 and fdf-client prints it -- [LEAF_ADDR_FROM_THE_REGION_V1], r16-a4;
    a leaf that names its own addr is refused EINVAL)

The WireGuard tunnel (in the app, the empty tunnel's config):

    [Interface]
    PrivateKey = <the laptop's private key>
    Address = <what fdf-client printed: "the region gave this leaf 10.252.X.Y/32">

    [Peer]
    PublicKey = <hub's public key: on the hub, wg show wg-frognet public-key>
    Endpoint = 165.232.153.144:50889
    AllowedIPs = 10.0.0.0/8
    PersistentKeepalive = 25

Then, in a terminal in `C:\fdf`:

    set RINA_RAM=165.232.153.144:51888
    fdf-client.exe

"Laptop joined the region ... with facts". On the hub: `event p`, the laptop's pubkey with allowed-ips its /32 (the one fdf-client printed),
`ip route`: that address dev wg-frognet. Activate the tunnel in the app, then `ping 10.170.170.1`, and from
10.170.170.1 `ping <that address>`. Names on the leaf (FrogNetHost.<name>) are Stage 4.4, not built yet: use addresses.
Leave fdf-client.exe running; closing it is leaving the region (the hub parks the peer within a second).

## 4. Later: 10.250.250.100 (AI-Host), a leaf

    cd / && tar xvzf - < /tmp/fdf-node.tgz
    apt-get install -y wireguard-tools
    wg genkey | tee /etc/wireguard/leaf.key | wg pubkey > /etc/wireguard/leaf.pub; chmod 600 /etc/wireguard/leaf.key
    cat > /opt/fdf/var/facts.json <<EOF
    {"role":"leaf","name":"AI-Host","guid":"ai-host","pubkey":"$(cat /etc/wireguard/leaf.pub)"}
    EOF
    cat > /etc/wireguard/wg-leaf.conf <<EOF
    [Interface]
    PrivateKey = $(cat /etc/wireguard/leaf.key)
    Address = <what fdf-client printed: "the region gave this leaf 10.252.X.Y/32">
    [Peer]
    PublicKey = <hub's public key>
    Endpoint = 165.232.153.144:50889
    AllowedIPs = 10.0.0.0/8
    PersistentKeepalive = 25
    EOF
    fdf-join 165.232.153.144:51888 && wg-quick up wg-leaf
    ping 10.170.170.1

## What to record (the plan's measurements)

- hub journal timestamps: facts -> peer, mapping -> prefix, leave -> parked, rejoin -> restored
- on a node: `journalctl -u frognet-tunnel-daemon-v3` from restart to "COMMIT_PHASE: status=exit" (merge wall time),
  and view.json's gen/dbhost after each event
- pull a node's network cable: hub "parked" line at ~20 s (keepalive); node's view.json phase=recalc, then steady with
  the LAN's dbhost; plug it back: rejoin, new gen
- `wg show wg-frognet` on the hub: N peers, 1 interface, 1 port, versus the broker's 2*N(N-1)/2 interfaces

## Known limits in this build

- The laptop's and AI-Host's names are not in anyone's /etc/hosts (4.4 not built): addresses only. Leaf addresses
  stay out of 10.252.254.0/24 (broker v4's veths on the droplet).
- The node daemon on region loss tears down ALL wg interfaces on that node (fine: a region-mode node has only the
  region's tunnel).
- A node is either on the broker or on the region, never both (mixed /30 + /16 on 10.253 is untested).
- If the droplet restarts fdf-ram-frognet, every member's session ends: every site splits to its LAN and rejoins. Plan it.
