/* SPDX-License-Identifier: MIT
 *
 * [WINTUN_BINDING_V1] written for FrogNet's fdf-tun (not the upstream golang.zx2c4.com/wintun, which could not be fetched
 * here): the API wireguard-go's tun_windows.go uses, straight onto wintun.dll's exports as declared in
 * github.com/WireGuard/wintun api/wintun.h. Errors are the DLL's GetLastError values (windows.Errno), which is what
 * tun_windows.go compares against (ERROR_NO_MORE_ITEMS, ERROR_HANDLE_EOF, ERROR_BUFFER_OVERFLOW, ERROR_INVALID_DATA).
 * wintun.dll (signed, from wintun.net) is loaded ONLY from the executable's own directory or System32 -- never the
 * search path (no DLL planting).
 */

package wintun

import (
	"fmt"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

type Adapter struct{ handle uintptr }
type Session struct{ handle uintptr }

var (
	loadOnce sync.Once
	loadErr  error
	procs    = map[string]uintptr{}
)

const loadFlags = windows.LOAD_LIBRARY_SEARCH_APPLICATION_DIR | windows.LOAD_LIBRARY_SEARCH_SYSTEM32

func load() error {
	loadOnce.Do(func() {
		h, err := windows.LoadLibraryEx("wintun.dll", 0, loadFlags)
		if err == windows.ERROR_BAD_EXE_FORMAT {
			loadErr = fmt.Errorf("the wintun.dll found is not for this machine (%v): this fdf-tun.exe needs bin\\amd64\\wintun.dll from wintun-0.14.1.zip (not x86, arm or arm64)", err)
			return
		}
		if err != nil {
			loadErr = fmt.Errorf("wintun.dll not found next to this program or in System32 (%v): get wintun-0.14.1.zip from https://www.wintun.net and put bin\\amd64\\wintun.dll beside it", err)
			return
		}
		for _, n := range []string{"WintunCreateAdapter", "WintunCloseAdapter", "WintunGetAdapterLUID", "WintunGetRunningDriverVersion",
			"WintunStartSession", "WintunEndSession", "WintunGetReadWaitEvent", "WintunReceivePacket", "WintunReleaseReceivePacket",
			"WintunAllocateSendPacket", "WintunSendPacket"} {
			p, err := windows.GetProcAddress(h, n)
			if err != nil {
				loadErr = fmt.Errorf("wintun.dll has no %s: %v", n, err)
				return
			}
			procs[n] = p
		}
	})
	return loadErr
}

func call(n string, a ...uintptr) (uintptr, syscall.Errno) {
	r, _, e := syscall.SyscallN(procs[n], a...)
	return r, e
}

// CreateAdapter: a new Wintun adapter (or the existing one of that name, reused).
func CreateAdapter(name, tunnelType string, requestedGUID *windows.GUID) (*Adapter, error) {
	if err := load(); err != nil {
		return nil, err
	}
	n, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return nil, err
	}
	t, err := windows.UTF16PtrFromString(tunnelType)
	if err != nil {
		return nil, err
	}
	r, e := call("WintunCreateAdapter", uintptr(unsafe.Pointer(n)), uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(requestedGUID)))
	if r == 0 {
		return nil, e
	}
	return &Adapter{handle: r}, nil
}

func (a *Adapter) Close() error {
	call("WintunCloseAdapter", a.handle)
	return nil
}

func (a *Adapter) LUID() (luid uint64) {
	call("WintunGetAdapterLUID", a.handle, uintptr(unsafe.Pointer(&luid)))
	return
}

func RunningVersion() (uint32, error) {
	if err := load(); err != nil {
		return 0, err
	}
	r, e := call("WintunGetRunningDriverVersion")
	if r == 0 {
		return 0, e
	}
	return uint32(r), nil
}

func (a *Adapter) StartSession(capacity uint32) (Session, error) {
	r, e := call("WintunStartSession", a.handle, uintptr(capacity))
	if r == 0 {
		return Session{}, e
	}
	return Session{handle: r}, nil
}

func (s Session) End() { call("WintunEndSession", s.handle) }

func (s Session) ReadWaitEvent() windows.Handle {
	r, _ := call("WintunGetReadWaitEvent", s.handle)
	return windows.Handle(r)
}

func (s Session) ReceivePacket() ([]byte, error) {
	var size uint32
	r, e := call("WintunReceivePacket", s.handle, uintptr(unsafe.Pointer(&size)))
	if r == 0 {
		return nil, e
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(r)), size), nil
}

func (s Session) ReleaseReceivePacket(p []byte) {
	call("WintunReleaseReceivePacket", s.handle, uintptr(unsafe.Pointer(&p[0])))
}

func (s Session) AllocateSendPacket(size int) ([]byte, error) {
	r, e := call("WintunAllocateSendPacket", s.handle, uintptr(size))
	if r == 0 {
		return nil, e
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(r)), size), nil
}

func (s Session) SendPacket(p []byte) {
	call("WintunSendPacket", s.handle, uintptr(unsafe.Pointer(&p[0])))
}
