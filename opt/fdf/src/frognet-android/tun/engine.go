// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// engine.go -- [BRIDGE_ONE_TUN_V1] one TUN (Android allows one VPN), one wireguard-go device per scope: the forward
// pond's, and one per island this member reaches. Two islands that both use 10.7.7.0/24 cannot be two peers of one
// WireGuard device (their allowed-ips would overlap), so each island is its own device, fed from the one TUN:
//
//	from the TUN, by longest prefix:  an island's alias block -> that island, destination alias -> real
//	                                  an island's own route     -> that island
//	                                  the forward pond's range  -> the forward device
//	                                  anything else             -> dropped (said once)
//	                                  every packet INTO an island: source -> the address that island gave this member
//	from an island:                   source real -> alias (when aliased), destination given -> this TUN's address
//	from the forward device:          to the TUN; destination given -> local when the forward pond gave one ("given")
//	into the forward device:          source local -> given, likewise (the phone's TUN address never changes)
//
// The whole desired state arrives in one "set" (the Kotlin planner decides it, as fdf-reach does on a node); a device is
// created, re-configured or closed to match. Gone is gone: an island not in the set is closed with its device.
// [SET_OUTSIDE_THE_PACKET_LOCK_V1] devices are configured with NO engine lock held: a peer being replaced waits for its
// receive routine, which may be delivering a packet that needs the engine's read lock (review 2026-10-11: a deadlock
// whenever an island's config changed under traffic). Only the swap of the tables takes the lock.
// [ICMP_INNER_REWRITTEN_V1] an ICMP error (unreachable, time exceeded, parameter problem) carries the start of the packet
// it is about; its addresses are mapped too, or the sender's stack cannot match it (path-MTU discovery black-holes).
package main

import (
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"net/netip"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/device"
	"golang.zx2c4.com/wireguard/tun"
)

// ---- a wireguard-go device's TUN side, fed from the engine ---------------------------------------------------------
type chanTun struct {
	name   string
	in     chan []byte
	out    func([]byte)
	events chan tun.Event
	closed chan struct{}
	once   sync.Once
}

func newChanTun(name string, out func([]byte)) *chanTun {
	c := &chanTun{name: name, in: make(chan []byte, 1024), out: out, events: make(chan tun.Event, 4), closed: make(chan struct{})}
	// no EventUp: wireguard-go's event reader would bring the device up on its own, racing configure's Up -- the loser
	// sees "already up" and a failed bind (an unprotected socket) was answered ok ([UP_IS_CONFIGURES_V1], found by the
	// --fd-socket stage of netns-fdftun-test)
	return c
}
func (c *chanTun) File() *os.File { return nil }
func (c *chanTun) Read(bufs [][]byte, sizes []int, offset int) (int, error) {
	select {
	case p := <-c.in:
		sizes[0] = copy(bufs[0][offset:], p)
		return 1, nil
	case <-c.closed:
		return 0, os.ErrClosed
	}
}
func (c *chanTun) Write(bufs [][]byte, offset int) (int, error) {
	for _, b := range bufs {
		c.out(b[offset:])
	}
	return len(bufs), nil
}
func (c *chanTun) MTU() (int, error)        { return 1420, nil }
func (c *chanTun) Name() (string, error)    { return c.name, nil }
func (c *chanTun) Events() <-chan tun.Event { return c.events }
func (c *chanTun) BatchSize() int           { return 1 }
func (c *chanTun) Close() error {
	c.once.Do(func() { close(c.closed); close(c.events) })
	return nil
}
func (c *chanTun) push(p []byte) bool { // a copy: the reader's buffer is reused
	q := make([]byte, len(p))
	copy(q, p)
	select {
	case c.in <- q:
		return true
	default:
		return false // full: dropped, as a full link drops
	}
}

// ---- configuration ---------------------------------------------------------------------------------------------------
type PeerCfg struct {
	Pub       string   `json:"pub"`
	Endpoint  string   `json:"endpoint"`
	Allowed   []string `json:"allowed"`
	Keepalive int      `json:"keepalive"`
}
type ForwardCfg struct {
	Key   string  `json:"key"`
	Peer  PeerCfg `json:"peer"`
	Given string  `json:"given,omitempty"` // this member's address in the forward pond; set and not local: rewritten as an island's
}
type IslandCfg struct {
	Area   string            `json:"area"`
	Key    string            `json:"key"`
	Given  string            `json:"given"` // this member's address IN the island (its region gave it)
	Peer   PeerCfg           `json:"peer"`  // the island's hub
	Routes []string          `json:"routes"`
	Alias  map[string]string `json:"alias"` // alias block -> real block
}
type SetCfg struct {
	Local   string      `json:"local"` // this TUN's address: what apps send from, what replies come back to
	Forward *ForwardCfg `json:"forward"`
	Islands []IslandCfg `json:"islands"`
}

func keyHex(b64 string) (string, error) {
	k, err := base64.StdEncoding.DecodeString(b64)
	if err != nil || len(k) != 32 {
		return "", fmt.Errorf("not a WireGuard key: %q", b64)
	}
	return hex.EncodeToString(k), nil
}

// uapi: the full text (the hub's key changed, or a new device) and the in-place update (same hub key: the peer and its
// session stay; only endpoint, keepalive and allowed-ips change)
func uapi(key string, p PeerCfg) (full, update, peer string, err error) {
	priv, err := keyHex(key)
	if err != nil {
		return "", "", "", err
	}
	if peer, err = keyHex(p.Pub); err != nil {
		return "", "", "", err
	}
	var b strings.Builder
	fmt.Fprintf(&b, "public_key=%s\nendpoint=%s\npersistent_keepalive_interval=%d\nreplace_allowed_ips=true\n", peer, p.Endpoint, p.Keepalive)
	for _, a := range p.Allowed {
		fmt.Fprintf(&b, "allowed_ip=%s\n", a)
	}
	return "private_key=" + priv + "\nreplace_peers=true\n" + b.String(), "private_key=" + priv + "\n" + b.String(), peer, nil
}

// ---- the engine ------------------------------------------------------------------------------------------------------
type wg struct {
	name     string
	ct       *chanTun
	dev      *device.Device
	conf     string // the full text it was last given AND brought up with: unchanged = not touched
	peer     string // that text's hub key
	endpoint string // that hub's endpoint (Windows: which interface its packets must leave by)
}

type amap struct{ alias, real netip.Prefix }

type island struct {
	w       *wg
	area    string
	given   netip.Addr
	routes  []netip.Prefix
	aliases []amap
}

type engine struct {
	t          tun.Device
	wmu        sync.Mutex // one writer to the TUN at a time
	setMu      sync.Mutex // one set at a time
	mu         sync.RWMutex
	local      netip.Addr
	fwd        *wg
	fwdAllowed []netip.Prefix
	fwdGiven   netip.Addr // invalid: the forward pond sees local itself
	islands    map[string]*island
	said       sync.Map // drop reasons said once
	drops      atomic.Uint64
	log        func(string, ...any)
}

func newEngine(t tun.Device, log func(string, ...any)) *engine {
	return &engine{t: t, islands: map[string]*island{}, log: log}
}

func (e *engine) toTun(p []byte) {
	e.wmu.Lock()
	defer e.wmu.Unlock()
	if _, err := e.t.Write([][]byte{p}, 0); err != nil {
		e.once("tunwrite", "writing to the TUN: %v", err)
	}
}

func (e *engine) once(key, f string, a ...any) {
	if _, loaded := e.said.LoadOrStore(key, true); !loaded {
		e.log(f, a...)
	}
}

// mostSpecific: the alias whose alias block (byAlias) or real block contains a
func mostSpecific(as []amap, a netip.Addr, byAlias bool) *amap {
	var best *amap
	for i := range as {
		x := &as[i]
		p := x.real
		if byAlias {
			p = x.alias
		}
		if p.Contains(a) && (best == nil || p.Bits() > pick(best, byAlias).Bits()) {
			best = x
		}
	}
	return best
}

func pick(m *amap, byAlias bool) netip.Prefix {
	if byAlias {
		return m.alias
	}
	return m.real
}

// fromTun: one packet an application sent (the TUN's read side)
func (e *engine) fromTun(p []byte) {
	_, dst, ok := addrs(p)
	if !ok {
		e.drops.Add(1)
		return
	}
	e.mu.RLock()
	defer e.mu.RUnlock()
	var best *island
	var bestAlias *amap
	bits := -1
	for _, is := range e.islands {
		for i := range is.aliases {
			if a := &is.aliases[i]; a.alias.Contains(dst) && a.alias.Bits() > bits {
				best, bestAlias, bits = is, a, a.alias.Bits()
			}
		}
		for _, r := range is.routes {
			if r.Contains(dst) && r.Bits() > bits && !is.aliased(r) {
				best, bestAlias, bits = is, nil, r.Bits()
			}
		}
	}
	if best != nil {
		if bestAlias != nil {
			setAddr(p, 16, mapAddr(dst, bestAlias.alias, bestAlias.real))
		}
		setAddr(p, 12, best.given)
		// an ICMP error this member sends about a packet from the island: that packet's source alias -> real, its
		// destination local -> given
		local := e.local
		innerAddrs(p, func(src, dst netip.Addr) (netip.Addr, netip.Addr) {
			if a := mostSpecific(best.aliases, src, true); a != nil {
				src = mapAddr(src, a.alias, a.real)
			}
			if dst == local {
				dst = best.given
			}
			return src, dst
		})
		if !best.w.ct.push(p) {
			e.drops.Add(1)
		}
		return
	}
	if e.fwd != nil {
		for _, r := range e.fwdAllowed {
			if r.Contains(dst) {
				if g := e.fwdGiven; g.IsValid() {
					setAddr(p, 12, g)
					local := e.local
					innerAddrs(p, func(src, dst netip.Addr) (netip.Addr, netip.Addr) {
						if dst == local {
							dst = g
						}
						return src, dst
					})
				}
				if !e.fwd.ct.push(p) {
					e.drops.Add(1)
				}
				return
			}
		}
	}
	e.drops.Add(1)
	if dst.IsMulticast() || dst == netip.AddrFrom4([4]byte{255, 255, 255, 255}) {
		return // the OS's own multicast/broadcast chatter on a new interface (IGMP, LLMNR, mDNS, SSDP): no scope carries it, nothing to say
	}
	e.once("nodest:"+dst.String(), "no scope routes %s: dropped (said once per destination)", dst)
}

func (is *island) aliased(r netip.Prefix) bool {
	for _, a := range is.aliases {
		if a.real == r {
			return true
		}
	}
	return false
}

// fromForward: one packet the forward device decrypted (for the forward pond's given address, when it is not local)
func (e *engine) fromForward(p []byte) {
	_, dst, ok := addrs(p)
	if !ok {
		return
	}
	e.mu.RLock()
	given, local := e.fwdGiven, e.local
	e.mu.RUnlock()
	if given.IsValid() {
		if dst != given {
			e.drops.Add(1)
			return
		}
		setAddr(p, 16, local)
		innerAddrs(p, func(src, dst netip.Addr) (netip.Addr, netip.Addr) {
			if src == given {
				src = local
			}
			return src, dst
		})
	}
	e.toTun(p)
}

// fromIsland: one packet the island's device decrypted
func (e *engine) fromIsland(area string, p []byte) {
	src, dst, ok := addrs(p)
	if !ok {
		return
	}
	e.mu.RLock()
	is := e.islands[area]
	local := e.local
	var al *amap
	var given netip.Addr
	var aliases []amap
	if is != nil {
		al = mostSpecific(is.aliases, src, false)
		given, aliases = is.given, is.aliases // read under the lock: set replaces them (a new slice, never written in place)
	}
	e.mu.RUnlock()
	if is == nil || dst != given {
		e.drops.Add(1)
		return
	}
	if al != nil {
		setAddr(p, 12, mapAddr(src, al.real, al.alias))
	}
	setAddr(p, 16, local)
	// an ICMP error from the island about a packet this member sent: that packet's source given -> local, its destination
	// real -> alias
	innerAddrs(p, func(isrc, idst netip.Addr) (netip.Addr, netip.Addr) {
		if isrc == given {
			isrc = local
		}
		if a := mostSpecific(aliases, idst, false); a != nil {
			idst = mapAddr(idst, a.real, a.alias)
		}
		return isrc, idst
	})
	e.toTun(p)
}

func (e *engine) newWG(name string, out func([]byte)) *wg {
	ct := newChanTun(name, out)
	// wireguard-go's own lines go to stderr with ours: stdout is the JSON control channel, one answer per request
	// ([STDOUT_IS_THE_CONTROL_CHANNEL_V1]; found on the phone: its ERROR lines read as a bad answer)
	logger := &device.Logger{Verbosef: device.DiscardLogf, Errorf: func(f string, a ...any) { e.log(name+": "+f, a...) }}
	return &wg{name: name, ct: ct, dev: device.NewDevice(ct, conn.NewDefaultBind(), logger)}
}

// configure: the full text when the hub's key changed (or none yet), else the in-place update. The text is recorded only
// once the device is up with it: a failed Up is tried again by the next set with the same text.
func (w *wg) configure(full, update, peer, endpoint string) error {
	if full == w.conf {
		return nil
	}
	w.endpoint = endpoint
	t := full
	if peer == w.peer && w.conf != "" {
		t = update
	}
	w.conf = ""
	if err := w.dev.IpcSet(t); err != nil {
		return err
	}
	if err := w.dev.Up(); err != nil {
		return err
	}
	if err := afterUp(w); err != nil {
		return err
	}
	w.conf, w.peer = full, peer
	return nil
}

func (w *wg) close() { w.dev.Close() }

// set: make the running devices what cfg says. All or nothing: anything refused or failing changes nothing that runs
// (a device re-configured before the failure is given its old text back; a new one is closed).
func (e *engine) set(cfg SetCfg) error {
	e.setMu.Lock()
	defer e.setMu.Unlock()
	local, err := netip.ParseAddr(cfg.Local)
	if err != nil || !local.Is4() {
		return fmt.Errorf("local %q is not an IPv4 address", cfg.Local)
	}
	type planned struct {
		full, update, peer string
		endpoint           string
		given              netip.Addr
		routes             []netip.Prefix
		aliases            []amap
	}
	want := map[string]*planned{}
	for _, ic := range cfg.Islands {
		pl := &planned{}
		if _, dup := want[ic.Area]; dup || ic.Area == "" {
			return fmt.Errorf("island %q: named twice, or not named", ic.Area)
		}
		if pl.given, err = netip.ParseAddr(ic.Given); err != nil || !pl.given.Is4() {
			return fmt.Errorf("island %s: given %q is not an IPv4 address", ic.Area, ic.Given)
		}
		for _, r := range ic.Routes {
			pr, err := netip.ParsePrefix(r)
			if err != nil {
				return fmt.Errorf("island %s: route %q: %v", ic.Area, r, err)
			}
			pl.routes = append(pl.routes, pr.Masked())
		}
		for a, r := range ic.Alias {
			pa, err1 := netip.ParsePrefix(a)
			pr, err2 := netip.ParsePrefix(r)
			if err1 != nil || err2 != nil || pa.Bits() != pr.Bits() {
				return fmt.Errorf("island %s: alias %s -> %s is not two prefixes of one length", ic.Area, a, r)
			}
			pl.aliases = append(pl.aliases, amap{pa.Masked(), pr.Masked()})
		}
		sort.Slice(pl.aliases, func(i, j int) bool { return pl.aliases[i].alias.String() < pl.aliases[j].alias.String() })
		p := ic.Peer
		p.Allowed = ic.Routes // the island's device carries exactly its real routes
		pl.endpoint = p.Endpoint
		if pl.full, pl.update, pl.peer, err = uapi(ic.Key, p); err != nil {
			return fmt.Errorf("island %s: %v", ic.Area, err)
		}
		want[ic.Area] = pl
	}
	// what one island takes from the TUN never overlaps what another takes: a destination would belong to two networks
	takes := func(pl *planned) (out []netip.Prefix) {
		for _, a := range pl.aliases {
			out = append(out, a.alias)
		}
		for _, r := range pl.routes {
			aliased := false
			for _, a := range pl.aliases {
				aliased = aliased || a.real == r
			}
			if !aliased {
				out = append(out, r)
			}
		}
		return
	}
	for a1, p1 := range want {
		for a2, p2 := range want {
			if a1 >= a2 {
				continue
			}
			for _, x := range takes(p1) {
				for _, y := range takes(p2) {
					if x.Overlaps(y) {
						return fmt.Errorf("islands %s and %s: %s overlaps %s", a1, a2, x, y)
					}
				}
			}
		}
	}
	var fwdFull, fwdUpdate, fwdPeer string
	var fwdAllowed []netip.Prefix
	var fwdGiven netip.Addr
	if cfg.Forward != nil {
		if cfg.Forward.Given != "" {
			if fwdGiven, err = netip.ParseAddr(cfg.Forward.Given); err != nil || !fwdGiven.Is4() {
				return fmt.Errorf("forward: given %q is not an IPv4 address", cfg.Forward.Given)
			}
			if fwdGiven == local {
				fwdGiven = netip.Addr{}
			}
		}
		if fwdFull, fwdUpdate, fwdPeer, err = uapi(cfg.Forward.Key, cfg.Forward.Peer); err != nil {
			return fmt.Errorf("forward: %v", err)
		}
		for _, a := range cfg.Forward.Peer.Allowed {
			pr, err := netip.ParsePrefix(a)
			if err != nil {
				return fmt.Errorf("forward allowed %q: %v", a, err)
			}
			fwdAllowed = append(fwdAllowed, pr.Masked())
		}
	}
	// ---- configure, NO engine lock held [SET_OUTSIDE_THE_PACKET_LOCK_V1]
	e.mu.RLock()
	fwd, cur := e.fwd, map[string]*island{}
	for a, is := range e.islands {
		cur[a] = is
	}
	e.mu.RUnlock()
	type undo struct {
		w          *wg
		conf, peer string
		created    bool
	}
	var undos []undo
	fail := func(what string, err error) error {
		for i := len(undos) - 1; i >= 0; i-- {
			u := undos[i]
			if u.created {
				u.w.close()
			} else if u.conf != "" {
				u.w.conf = ""
				if e2 := u.w.configure(u.conf, u.conf, u.peer, u.w.endpoint); e2 != nil {
					e.log("%s: could not be given its old configuration back: %v", u.w.name, e2)
				}
			}
		}
		return fmt.Errorf("%s: %v", what, err)
	}
	newFwd := fwd
	if cfg.Forward != nil {
		if newFwd == nil {
			newFwd = e.newWG("forward", e.fromForward)
			undos = append(undos, undo{w: newFwd, created: true})
		} else {
			undos = append(undos, undo{w: newFwd, conf: newFwd.conf, peer: newFwd.peer})
		}
		if err := newFwd.configure(fwdFull, fwdUpdate, fwdPeer, cfg.Forward.Peer.Endpoint); err != nil {
			return fail("forward", err)
		}
	} else {
		newFwd = nil
	}
	wgs := map[string]*wg{}
	for area, pl := range want {
		w := (*wg)(nil)
		if is := cur[area]; is != nil {
			w = is.w
			undos = append(undos, undo{w: w, conf: w.conf, peer: w.peer})
		} else {
			a := area
			w = e.newWG("island-"+area, func(p []byte) { e.fromIsland(a, p) })
			undos = append(undos, undo{w: w, created: true})
		}
		if err := w.configure(pl.full, pl.update, pl.peer, pl.endpoint); err != nil {
			return fail("island "+area, err)
		}
		wgs[area] = w
	}
	// ---- the swap: tables only, under the lock
	var closing []*wg
	e.mu.Lock()
	e.local = local
	if fwd != nil && newFwd == nil {
		closing = append(closing, fwd)
		e.log("forward: down")
	}
	e.fwd, e.fwdAllowed, e.fwdGiven = newFwd, fwdAllowed, fwdGiven
	next := map[string]*island{}
	for area, pl := range want {
		next[area] = &island{w: wgs[area], area: area, given: pl.given, routes: pl.routes, aliases: pl.aliases}
	}
	for area, is := range e.islands {
		if _, ok := next[area]; !ok {
			closing = append(closing, is.w)
			e.log("island %s: down (gone is gone)", area)
		}
	}
	e.islands = next
	e.mu.Unlock()
	for _, w := range closing { // after the lock: a closing device waits for its routines
		w.close()
	}
	return nil
}

// status: per device, the hub's last handshake and the bytes each way (read outside the engine lock)
func (e *engine) status() map[string]any {
	e.mu.RLock()
	local, fwd, drops := e.local, e.fwd, e.drops.Load()
	isl := map[string]*island{}
	for a, is := range e.islands {
		isl[a] = is
	}
	e.mu.RUnlock()
	one := func(w *wg) map[string]any {
		out := map[string]any{}
		s, err := w.dev.IpcGet()
		if err != nil {
			out["error"] = err.Error()
			return out
		}
		for _, ln := range strings.Split(s, "\n") {
			k, v, ok := strings.Cut(ln, "=")
			if !ok {
				continue
			}
			switch k {
			case "last_handshake_time_sec":
				if n, _ := strconv.ParseInt(v, 10, 64); n > 0 {
					out["handshake_age_s"] = time.Now().Unix() - n
				}
			case "rx_bytes", "tx_bytes":
				n, _ := strconv.ParseInt(v, 10, 64)
				out[k] = n
			case "endpoint":
				out[k] = v
			}
		}
		return out
	}
	r := map[string]any{"local": local.String(), "drops": drops}
	if fwd != nil {
		r["forward"] = one(fwd)
	}
	m := map[string]any{}
	for a, is := range isl {
		o := one(is.w)
		o["given"] = is.given.String()
		m[a] = o
	}
	r["islands"] = m
	return r
}

func (e *engine) closeAll() { // closed outside the lock, as in set
	e.setMu.Lock()
	defer e.setMu.Unlock()
	e.mu.Lock()
	var all []*wg
	if e.fwd != nil {
		all = append(all, e.fwd)
	}
	for _, is := range e.islands {
		all = append(all, is.w)
	}
	e.fwd, e.islands = nil, map[string]*island{}
	e.mu.Unlock()
	for _, w := range all {
		w.close()
	}
}
