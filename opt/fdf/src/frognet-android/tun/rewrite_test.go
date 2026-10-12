// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package main

import (
	"encoding/binary"
	"math/rand"
	"net/netip"
	"testing"
)

// full checksums, computed from scratch: the oracle for the incremental ones
func sum16(b []byte, s uint32) uint32 {
	for i := 0; i+1 < len(b); i += 2 {
		s += uint32(binary.BigEndian.Uint16(b[i:]))
	}
	if len(b)%2 == 1 {
		s += uint32(b[len(b)-1]) << 8
	}
	return s
}
func fold(s uint32) uint16 {
	for s>>16 != 0 {
		s = (s & 0xffff) + (s >> 16)
	}
	return ^uint16(s)
}
func ipHdrSum(p []byte) uint16 {
	ihl := int(p[0]&0x0f) * 4
	h := append([]byte{}, p[:ihl]...)
	h[10], h[11] = 0, 0
	return fold(sum16(h, 0))
}
func l4Sum(p []byte) uint16 {
	ihl := int(p[0]&0x0f) * 4
	seg := append([]byte{}, p[ihl:]...)
	off := 16
	if p[9] == 17 {
		off = 6
	}
	seg[off], seg[off+1] = 0, 0
	ph := make([]byte, 12)
	copy(ph[0:8], p[12:20])
	ph[9] = p[9]
	binary.BigEndian.PutUint16(ph[10:], uint16(len(seg)))
	c := fold(sum16(seg, sum16(ph, 0)))
	if p[9] == 17 && c == 0 {
		c = 0xffff
	}
	return c
}

func packet(r *rand.Rand, proto byte, src, dst netip.Addr, n int) []byte {
	ihl := 20
	l4 := 20
	if proto == 17 {
		l4 = 8
	}
	p := make([]byte, ihl+l4+n)
	p[0], p[9] = 0x45, proto
	binary.BigEndian.PutUint16(p[2:], uint16(len(p)))
	p[8] = 64
	s, d := src.As4(), dst.As4()
	copy(p[12:16], s[:])
	copy(p[16:20], d[:])
	r.Read(p[ihl:])
	if proto == 17 {
		binary.BigEndian.PutUint16(p[ihl+4:], uint16(l4+n))
	}
	binary.BigEndian.PutUint16(p[10:], ipHdrSum(p))
	off := 16
	if proto == 17 {
		off = 6
	}
	binary.BigEndian.PutUint16(p[ihl+off:], l4Sum(p))
	return p
}

func TestRewriteChecksums(t *testing.T) {
	r := rand.New(rand.NewSource(7))
	a := func(s string) netip.Addr { return netip.MustParseAddr(s) }
	for i := 0; i < 20000; i++ {
		proto := byte(6)
		if i%2 == 1 {
			proto = 17
		}
		src := netip.AddrFrom4([4]byte{10, byte(r.Intn(256)), byte(r.Intn(256)), byte(r.Intn(256))})
		dst := netip.AddrFrom4([4]byte{100, byte(64 + r.Intn(64)), byte(r.Intn(256)), byte(r.Intn(256))})
		p := packet(r, proto, src, dst, r.Intn(1400))
		setAddr(p, 16, netip.AddrFrom4([4]byte{10, 7, 7, byte(r.Intn(256))}))
		setAddr(p, 12, a("10.252.0.1"))
		ihl := 20
		off := 16
		if proto == 17 {
			off = 6
		}
		if got, want := binary.BigEndian.Uint16(p[10:]), ipHdrSum(p); got != want {
			t.Fatalf("packet %d: IPv4 header checksum %04x, recomputed %04x", i, got, want)
		}
		if got, want := binary.BigEndian.Uint16(p[ihl+off:]), l4Sum(p); got != want {
			t.Fatalf("packet %d proto %d: L4 checksum %04x, recomputed %04x", i, proto, got, want)
		}
	}
}

func TestUDPZeroChecksumStaysZero(t *testing.T) {
	r := rand.New(rand.NewSource(1))
	p := packet(r, 17, netip.MustParseAddr("10.1.1.1"), netip.MustParseAddr("100.64.0.9"), 40)
	binary.BigEndian.PutUint16(p[26:], 0) // sender sent no checksum
	setAddr(p, 16, netip.MustParseAddr("10.7.7.9"))
	if binary.BigEndian.Uint16(p[26:]) != 0 {
		t.Fatal("a UDP packet sent without a checksum got one")
	}
}

func TestLaterFragmentLeavesPayloadAlone(t *testing.T) {
	r := rand.New(rand.NewSource(2))
	p := packet(r, 6, netip.MustParseAddr("10.1.1.1"), netip.MustParseAddr("100.64.0.9"), 64)
	binary.BigEndian.PutUint16(p[6:], 185) // fragment offset 185*8: no TCP header in this packet
	binary.BigEndian.PutUint16(p[10:], ipHdrSum(p))
	before := append([]byte{}, p[20:]...)
	setAddr(p, 16, netip.MustParseAddr("10.7.7.9"))
	if string(before) != string(p[20:]) {
		t.Fatal("a later fragment's payload was touched")
	}
	if binary.BigEndian.Uint16(p[10:]) != ipHdrSum(p) {
		t.Fatal("header checksum wrong on a fragment")
	}
}

func TestMapAddr(t *testing.T) {
	cases := [][4]string{
		{"10.7.7.42", "10.7.7.0/24", "100.64.2.0/24", "100.64.2.42"},
		{"100.64.2.42", "100.64.2.0/24", "10.7.7.0/24", "10.7.7.42"},
		{"10.252.0.1", "10.252.0.1/32", "100.64.5.7/32", "100.64.5.7"},
	}
	for _, c := range cases {
		if got := mapAddr(netip.MustParseAddr(c[0]), netip.MustParsePrefix(c[1]), netip.MustParsePrefix(c[2])); got.String() != c[3] {
			t.Fatalf("%s in %s -> %s: got %s, want %s", c[0], c[1], c[2], got, c[3])
		}
	}
}

func TestICMPErrorInnerRewrite(t *testing.T) {
	r := rand.New(rand.NewSource(3))
	a := netip.MustParseAddr
	for i := 0; i < 5000; i++ {
		inner := packet(r, 17, a("10.252.0.1"), netip.AddrFrom4([4]byte{10, 7, 7, byte(r.Intn(256))}), 8)
		quoted := inner[:28]
		types := []byte{3, 11, 12}
		p := make([]byte, 20+8+len(quoted))
		p[0], p[9], p[8] = 0x45, 1, 64
		binary.BigEndian.PutUint16(p[2:], uint16(len(p)))
		copy(p[12:16], []byte{10, 7, 7, 254})
		copy(p[16:20], []byte{10, 252, 0, 1})
		p[20], p[21] = types[i%3], byte(r.Intn(16))
		copy(p[28:], quoted)
		binary.BigEndian.PutUint16(p[10:], ipHdrSum(p))
		binary.BigEndian.PutUint16(p[22:], fold(sum16(p[20:], 0)))
		innerAddrs(p, func(s, d netip.Addr) (netip.Addr, netip.Addr) {
			return a("100.127.255.1"), mapAddr(d, netip.MustParsePrefix("10.7.7.0/24"), netip.MustParsePrefix("100.64.3.0/24"))
		})
		in := p[28:]
		if got := netip.AddrFrom4([4]byte(in[12:16])); got != a("100.127.255.1") {
			t.Fatalf("quoted source %s", got)
		}
		if got := netip.AddrFrom4([4]byte(in[16:20])); !netip.MustParsePrefix("100.64.3.0/24").Contains(got) {
			t.Fatalf("quoted destination %s", got)
		}
		if got, want := binary.BigEndian.Uint16(in[10:]), ipHdrSum(in); got != want {
			t.Fatalf("quoted header checksum %04x, recomputed %04x", got, want)
		}
		c := append([]byte{}, p[20:]...)
		c[2], c[3] = 0, 0
		if got, want := binary.BigEndian.Uint16(p[22:]), fold(sum16(c, 0)); got != want {
			t.Fatalf("ICMP checksum %04x, recomputed %04x", got, want)
		}
	}
	// an echo reply is not an error: untouched
	q := make([]byte, 64)
	q[0], q[9], q[20] = 0x45, 1, 0
	before := append([]byte{}, q...)
	innerAddrs(q, func(s, d netip.Addr) (netip.Addr, netip.Addr) { return a("1.1.1.1"), a("2.2.2.2") })
	if string(before) != string(q) {
		t.Fatal("an echo reply was rewritten")
	}
}
