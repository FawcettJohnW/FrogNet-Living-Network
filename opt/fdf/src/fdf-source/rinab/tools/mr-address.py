#!/usr/bin/env python3
"""mr-address.py NAME... -- [MR_ADDRESS_FROM_NAME_V1] the rule in experiments/DESIGN-MR-RULE.md, independently of the C++
(rina-lisp mr-address): SHA-256 of the name; IPv4 198.18.0.0/15 + first 17 bits (never .0 or .255); IPv6 fd46:4446::/32 + first 96 bits."""
import hashlib, ipaddress, sys
for n in sys.argv[1:]:
    h = hashlib.sha256(n.encode()).digest()
    v = ((h[0] << 9) | (h[1] << 1) | (h[2] >> 7)) & 0x1FFFF
    if v & 0xFF == 0: v |= 1
    elif v & 0xFF == 0xFF: v ^= 1
    print(n, ipaddress.IPv4Address(0xC6120000 + v), ipaddress.IPv6Address(bytes([0xFD, 0x46, 0x44, 0x46]) + h[:12]))
