//go:build linux

/* SPDX-License-Identifier: MIT
 *
 * [FDF_PROTECT_HOOK_V1] added for FrogNet's fdf-tun (not upstream): on Android a WireGuard socket must be protected from
 * the VPN it serves (VpnService.protect) BEFORE it sends anything, or its packets can loop into the tunnel. Upstream does
 * this only in its Android build (PeekLookAtSocketFd, after the bind). fdf-tun is a static linux binary run by the app,
 * so it sets ProtectSocket: called for every socket wireguard-go opens, before bind; an error fails the bind.
 */

package conn

import "syscall"

// ProtectSocket, when set, is called with each new socket's fd before it is bound.
var ProtectSocket func(fd int) error

func init() {
	controlFns = append(controlFns, func(network, address string, c syscall.RawConn) error {
		if ProtectSocket == nil {
			return nil
		}
		var perr error
		if err := c.Control(func(fd uintptr) { perr = ProtectSocket(int(fd)) }); err != nil {
			return err
		}
		return perr
	})
}
