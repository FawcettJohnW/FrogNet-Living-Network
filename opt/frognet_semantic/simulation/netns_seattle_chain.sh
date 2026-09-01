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
set -e
PATH=$PATH:/usr/sbin
for n in s5 s6 s2 nygw ny1 ny2gw ny2; do ip netns del $n 2>/dev/null || true; done
for n in s5 s6 s2 nygw ny1 ny2gw ny2; do ip netns add $n; ip -n $n link set lo up; done

mk(){ ip link add $1 type veth peer name $2; ip link set $1 netns $3; ip link set $2 netns $4
      ip -n $3 addr add $5 dev $1; ip -n $3 link set $1 up
      ip -n $4 addr add $6 dev $2; ip -n $4 link set $2 up; }

# Seattle chain: s5 -> s6 -> s2, each child holding a lease pointing UP
mk a1 b1 s5 s6   10.250.250.1/24  10.250.250.221/24
mk a2 b2 s6 s2   10.160.160.1/24  10.160.160.47/24
ip -n s2 addr add 10.120.120.1/24 dev lo

# the TUNNEL: s5 <-> nygw on the 10.253 transit plane. nygw is a ROUTER; the
# destination lives BEHIND it, which is what makes the tunnel hop visible.
mk t1 t2 s5 nygw 10.253.200.30/30 10.253.200.29/30
mk n1 n2 nygw ny1 10.102.60.254/24 10.102.60.1/24

# a second tunnel hop, as NY-2 shows: nygw -> ny2gw -> ny2
mk t3 t4 nygw ny2gw 10.253.200.241/30 10.253.200.242/30
mk n3 n4 ny2gw ny2  10.28.28.254/24 10.28.28.1/24

for n in s5 s6 s2 nygw ny1 ny2gw ny2; do ip netns exec $n sysctl -qw net.ipv4.ip_forward=1; done

ip -n s5   route add 10.160.160.0/24 via 10.250.250.221
ip -n s5   route add 10.120.120.0/24 via 10.250.250.221
ip -n s5   route add 10.102.60.0/24  via 10.253.200.29
ip -n s5   route add 10.28.28.0/24   via 10.253.200.29
ip -n s6   route add 10.120.120.0/24 via 10.160.160.47
ip -n s6   route add 10.0.0.0/8      via 10.250.250.1
ip -n s2   route add 10.0.0.0/8      via 10.160.160.1
ip -n nygw route add 10.28.28.0/24   via 10.253.200.242
ip -n nygw route add 10.0.0.0/8      via 10.253.200.30
ip -n ny1  route add 10.0.0.0/8      via 10.102.60.254
ip -n ny2gw route add 10.0.0.0/8     via 10.253.200.241
ip -n ny2  route add 10.0.0.0/8      via 10.28.28.254
echo TOPO2-UP
