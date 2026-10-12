// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// rewrite.go -- [BRIDGE_REWRITE_V1] the 1:1 address rewrite at the boundary, in userspace (what NETMAP + SNAT + DNAT do
// on a node). Stateless: every rewrite is a pure function of the packet and the island's maps. IPv4 only (the mesh is
// IPv4). Checksums are adjusted incrementally (RFC 1624): the IPv4 header's, and TCP's / UDP's over the pseudo-header;
// only a packet's first fragment carries the L4 header. Addresses carried INSIDE payloads (FTP PORT, SIP SDP, ...) are not
// rewritten -- FrogNet's own applications name endpoints by identity (w:<guid>:<port>), resolved per scope instead.
package main

import (
	"encoding/binary"
	"net/netip"
)

// mapAddr: addr's position in from, applied to to (the same prefix length): 10.7.7.42 in 10.7.7.0/24 -> 100.64.2.42
func mapAddr(a netip.Addr, from, to netip.Prefix) netip.Addr {
	f, t, x := from.Masked().Addr().As4(), to.Masked().Addr().As4(), a.As4()
	off := binary.BigEndian.Uint32(x[:]) - binary.BigEndian.Uint32(f[:])
	var o [4]byte
	binary.BigEndian.PutUint32(o[:], binary.BigEndian.Uint32(t[:])+off)
	return netip.AddrFrom4(o)
}

// csumAdjust: RFC 1624 eqn 3, HC' = ~(~HC + ~m + m'), for one 4-byte field replaced
func csumAdjust(c uint16, old, nu [4]byte) uint16 {
	sum := uint32(^c)
	for i := 0; i < 4; i += 2 {
		sum += uint32(^binary.BigEndian.Uint16(old[i:])) & 0xffff
		sum += uint32(binary.BigEndian.Uint16(nu[i:]))
	}
	for sum>>16 != 0 {
		sum = (sum & 0xffff) + (sum >> 16)
	}
	return ^uint16(sum)
}

// srcOf / dstOf: an IPv4 packet's addresses (ok false: not IPv4, or too short)
func addrs(p []byte) (src, dst netip.Addr, ok bool) {
	if len(p) < 20 || p[0]>>4 != 4 {
		return
	}
	return netip.AddrFrom4([4]byte(p[12:16])), netip.AddrFrom4([4]byte(p[16:20])), true
}

// setAddr: replace the source (field 12) or destination (field 16) of an IPv4 packet in place, fixing every checksum
func setAddr(p []byte, field int, nu netip.Addr) {
	var old [4]byte
	copy(old[:], p[field:field+4])
	n4 := nu.As4()
	if old == n4 {
		return
	}
	copy(p[field:field+4], n4[:])
	binary.BigEndian.PutUint16(p[10:12], csumAdjust(binary.BigEndian.Uint16(p[10:12]), old, n4))
	ihl := int(p[0]&0x0f) * 4
	if binary.BigEndian.Uint16(p[6:8])&0x1fff != 0 || ihl < 20 {
		return // a later fragment: no L4 header here
	}
	switch p[9] {
	case 6: // TCP: checksum at +16, always present
		if len(p) >= ihl+18 {
			binary.BigEndian.PutUint16(p[ihl+16:], csumAdjust(binary.BigEndian.Uint16(p[ihl+16:]), old, n4))
		}
	case 17: // UDP: checksum at +6; 0 = none (IPv4), and a computed 0 is sent as 0xffff
		if len(p) >= ihl+8 {
			c := binary.BigEndian.Uint16(p[ihl+6:])
			if c != 0 {
				c = csumAdjust(c, old, n4)
				if c == 0 {
					c = 0xffff
				}
				binary.BigEndian.PutUint16(p[ihl+6:], c)
			}
		}
	}
}

// csumAdjust16: RFC 1624 for one 2-byte field replaced
func csumAdjust16(c uint16, old, nu [2]byte) uint16 {
	sum := uint32(^c) + uint32(^binary.BigEndian.Uint16(old[:]))&0xffff + uint32(binary.BigEndian.Uint16(nu[:]))
	for sum>>16 != 0 {
		sum = (sum & 0xffff) + (sum >> 16)
	}
	return ^uint16(sum)
}

// innerAddrs: [ICMP_INNER_REWRITTEN_V1] an ICMP error (3 unreachable, 11 time exceeded, 12 parameter problem) quotes the
// packet it is about; f maps that packet's (source, destination). The quoted header's checksum and the ICMP checksum are
// adjusted with it. (The quoted UDP/TCP checksum is left: no stack checks a quoted segment's checksum.)
func innerAddrs(p []byte, f func(src, dst netip.Addr) (netip.Addr, netip.Addr)) {
	if len(p) < 20 || p[0]>>4 != 4 || p[9] != 1 || binary.BigEndian.Uint16(p[6:8])&0x1fff != 0 {
		return
	}
	ihl := int(p[0]&0x0f) * 4
	if ihl < 20 || len(p) < ihl+8+20 {
		return
	}
	if t := p[ihl]; t != 3 && t != 11 && t != 12 {
		return
	}
	in := p[ihl+8:]
	if in[0]>>4 != 4 || int(in[0]&0x0f)*4 < 20 {
		return
	}
	s, d := netip.AddrFrom4([4]byte(in[12:16])), netip.AddrFrom4([4]byte(in[16:20]))
	ns, nd := f(s, d)
	setInner(p, ihl, 12, ns)
	setInner(p, ihl, 16, nd)
}

func setInner(p []byte, ihl, field int, nu netip.Addr) {
	in := p[ihl+8:]
	var old [4]byte
	copy(old[:], in[field:field+4])
	n4 := nu.As4()
	if old == n4 {
		return
	}
	oldCs := [2]byte{in[10], in[11]}
	copy(in[field:field+4], n4[:])
	ncs := csumAdjust(binary.BigEndian.Uint16(in[10:12]), old, n4)
	binary.BigEndian.PutUint16(in[10:12], ncs)
	ic := csumAdjust(binary.BigEndian.Uint16(p[ihl+2:]), old, n4) // the ICMP checksum covers the quoted bytes
	ic = csumAdjust16(ic, oldCs, [2]byte{in[10], in[11]})
	binary.BigEndian.PutUint16(p[ihl+2:], ic)
}
