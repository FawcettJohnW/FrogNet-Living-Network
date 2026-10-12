//go:build windows

// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// main_windows.go -- [BRIDGE_WINDOWS_V1] the TUN on Windows: a Wintun adapter (wireguard-go's tun_windows on
// wintun.dll; Administrator). Windows has no VpnService.protect: instead, after a device is up, if the route Windows
// would use for its hub's endpoint is THIS TUN (a hub inside 10.0.0.0/8 or 100.64.0.0/10 reached through a gateway),
// its socket is bound to the interface of the default route (IP_UNICAST_IF), so its packets never loop into the
// tunnel they carry ([NO_LOOP_ON_WINDOWS_V1]). Said in the log either way.
package main

import (
	"errors"
	"fmt"
	"net"
	"net/netip"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/tun"
)

const tunMTU = 1280 // the same for every scope, as on the phone

var tunLUID uint64

func openTun(tunName, fdSock string) (tun.Device, string, error) {
	if fdSock != "" {
		return nil, "", errors.New("--fd-socket is Android's; on Windows give --tun NAME")
	}
	if tunName == "" {
		return nil, "", errors.New("give --tun NAME")
	}
	t, err := tun.CreateTUN(tunName, tunMTU)
	if err != nil {
		return nil, "", fmt.Errorf("creating the Wintun adapter %q (run as Administrator; wintun.dll beside fdf-tun.exe): %v", tunName, err)
	}
	if nt, ok := t.(*tun.NativeTun); ok {
		tunLUID = nt.LUID()
		if v, err := nt.RunningVersion(); err == nil {
			logf("Wintun driver %d.%d", v>>16&0xffff, v&0xffff)
		}
	}
	return t, tunName, nil
}

// adapters: every adapter's (LUID, IfIndex, Ipv4Metric, has a gateway, up)
type adapter struct {
	luid    uint64
	index   uint32
	metric  uint32
	gateway bool
	up      bool
	name    string
}

func adapters() ([]adapter, error) {
	size := uint32(15000)
	for i := 0; i < 3; i++ {
		b := make([]byte, size)
		a := (*windows.IpAdapterAddresses)(unsafe.Pointer(&b[0]))
		err := windows.GetAdaptersAddresses(windows.AF_INET, windows.GAA_FLAG_INCLUDE_GATEWAYS, 0, a, &size)
		if err == windows.ERROR_BUFFER_OVERFLOW {
			continue
		}
		if err != nil {
			return nil, err
		}
		var out []adapter
		for ; a != nil; a = a.Next {
			out = append(out, adapter{luid: a.Luid, index: a.IfIndex, metric: a.Ipv4Metric, gateway: a.FirstGatewayAddress != nil,
				up: a.OperStatus == windows.IfOperStatusUp, name: windows.UTF16PtrToString(a.FriendlyName)})
		}
		return out, nil
	}
	return nil, errors.New("GetAdaptersAddresses: buffer")
}

func afterUp(w *wg) error {
	ap, err := netip.ParseAddrPort(w.endpoint)
	if err != nil || !ap.Addr().Is4() {
		return nil // no endpoint yet, or IPv6: nothing routes it into this TUN
	}
	all, err := adapters()
	if err != nil {
		return fmt.Errorf("%s: reading the adapters: %v", w.name, err)
	}
	var tunIndex uint32
	for _, a := range all {
		if a.luid == tunLUID {
			tunIndex = a.index
		}
	}
	var best uint32
	ip := ap.Addr().As4()
	if err := windows.GetBestInterfaceEx(&windows.SockaddrInet4{Addr: ip}, &best); err != nil {
		return fmt.Errorf("%s: the route to %s: %v", w.name, ap.Addr(), err)
	}
	if best != tunIndex {
		return nil // Windows already sends it elsewhere (a public hub, or a hub on an attached LAN)
	}
	var out *adapter
	for i := range all {
		a := &all[i]
		if a.luid != tunLUID && a.up && a.gateway && (out == nil || a.metric < out.metric) {
			out = a
		}
	}
	if out == nil {
		return fmt.Errorf("%s: the hub %s would be routed into this TUN and no other interface has a default route", w.name, ap.Addr())
	}
	b, ok := w.dev.Bind().(conn.BindSocketToInterface)
	if !ok {
		return fmt.Errorf("%s: this bind cannot be held to an interface", w.name)
	}
	if err := b.BindSocketToInterface4(out.index, false); err != nil && !errors.Is(err, net.ErrClosed) {
		return fmt.Errorf("%s: holding its socket to %s: %v", w.name, out.name, err)
	}
	logf("%s: hub %s would route into this TUN: its socket is held to %q (index %d)", w.name, ap.Addr(), out.name, out.index)
	return nil
}
