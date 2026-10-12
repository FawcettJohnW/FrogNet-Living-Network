//go:build linux

// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// main_linux.go -- the TUN on Linux and Android: created by name (netns tests, a Linux machine) or received from the
// app (--fd-socket: Android's VpnService), and the protect requests back to the app ([FDF_PROTECT_HOOK_V1]).
package main

import (
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	"golang.org/x/sys/unix"
	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/tun"
)

func openTun(tunName, fdSock string) (tun.Device, string, error) {
	var fd int
	var err error
	switch {
	case tunName != "":
		fd, err = createTun(tunName)
	case fdSock != "":
		var link *appLink
		link, fd, err = acceptApp(fdSock)
		if err == nil {
			conn.ProtectSocket = link.protect
		}
	default:
		err = errors.New("give --tun NAME or --fd-socket NAME")
	}
	if err != nil {
		return nil, "", err
	}
	t, name, err := tun.CreateUnmonitoredTUNFromFD(fd)
	if err != nil {
		return nil, "", fmt.Errorf("TUN fd %d: %v", fd, err)
	}
	return t, name, nil
}

// afterUp: nothing on Linux (Android's sockets were protected before they bound)
func afterUp(w *wg) error { return nil }

// a TUN created here: IFF_TUN | IFF_NO_PI, no IFF_VNET_HDR -- the same shape as an Android VpnService TUN
func createTun(name string) (int, error) {
	fd, err := unix.Open("/dev/net/tun", unix.O_RDWR|unix.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	ifr, err := unix.NewIfreq(name)
	if err != nil {
		unix.Close(fd)
		return -1, err
	}
	ifr.SetUint16(unix.IFF_TUN | unix.IFF_NO_PI)
	if err := unix.IoctlIfreq(fd, unix.TUNSETIFF, ifr); err != nil {
		unix.Close(fd)
		return -1, fmt.Errorf("TUNSETIFF %s: %v", name, err)
	}
	return fd, nil
}

// ---- Android: the TUN fd from the app, and protect requests back to it -------------------------------------------
type appLink struct {
	mu  sync.Mutex
	fd  int
	seq int
}

func acceptApp(name string) (*appLink, int, error) {
	s, err := unix.Socket(unix.AF_UNIX, unix.SOCK_SEQPACKET|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, -1, err
	}
	if err := unix.Bind(s, &unix.SockaddrUnix{Name: "@" + name}); err != nil {
		return nil, -1, fmt.Errorf("bind @%s: %v", name, err)
	}
	if err := unix.Listen(s, 1); err != nil {
		return nil, -1, err
	}
	fmt.Println(`{"ok":true,"listening":"@` + name + `"}`) // the app connects after this line
	c, _, err := unix.Accept4(s, unix.SOCK_CLOEXEC)
	unix.Close(s)
	if err != nil {
		return nil, -1, err
	}
	cred, err := unix.GetsockoptUcred(c, unix.SOL_SOCKET, unix.SO_PEERCRED)
	if err != nil || int(cred.Uid) != os.Getuid() {
		unix.Close(c)
		return nil, -1, fmt.Errorf("the peer on @%s is not this app (uid %v): refused", name, cred)
	}
	buf, oob := make([]byte, 64), make([]byte, unix.CmsgSpace(4))
	_, oobn, _, _, err := unix.Recvmsg(c, buf, oob, 0)
	if err != nil {
		return nil, -1, err
	}
	msgs, err := unix.ParseSocketControlMessage(oob[:oobn])
	if err != nil || len(msgs) == 0 {
		return nil, -1, errors.New("the app sent no TUN fd")
	}
	fds, err := unix.ParseUnixRights(&msgs[0])
	if err != nil || len(fds) != 1 {
		return nil, -1, errors.New("the app sent no TUN fd")
	}
	return &appLink{fd: c}, fds[0], nil
}

// protect: "protect <n>" with the socket goes to the app, which protects it and answers "ok <n>" (or "no <n>") within 5 s;
// anything else fails the bind. The number pairs answer with request: an answer that came too late for its socket is
// read and dropped, never taken for the next one's (review 2026-10-11).
func (a *appLink) protect(fd int) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.seq++
	want := fmt.Sprintf("ok %d", a.seq)
	err := unix.Sendmsg(a.fd, []byte(fmt.Sprintf("protect %d", a.seq)), unix.UnixRights(fd), nil, 0)
	for err == unix.EINTR {
		err = unix.Sendmsg(a.fd, []byte(fmt.Sprintf("protect %d", a.seq)), unix.UnixRights(fd), nil, 0)
	}
	if err != nil {
		return fmt.Errorf("sending socket %d to be protected: %v", fd, err)
	}
	deadline := time.Now().Add(5 * time.Second)
	b := make([]byte, 32)
	for {
		left := time.Until(deadline)
		if left <= 0 {
			return fmt.Errorf("the app did not protect socket %d within 5 s", fd)
		}
		tv := unix.NsecToTimeval(left.Nanoseconds())
		unix.SetsockoptTimeval(a.fd, unix.SOL_SOCKET, unix.SO_RCVTIMEO, &tv)
		n, err := unix.Read(a.fd, b)
		if err == unix.EINTR {
			continue // a signal (the Go runtime's own) interrupted the wait: wait again, to the same deadline
		}
		if err == unix.EAGAIN || err == unix.EWOULDBLOCK {
			return fmt.Errorf("the app did not protect socket %d within 5 s", fd)
		}
		if err != nil || n <= 0 {
			return fmt.Errorf("the app did not protect socket %d (%v)", fd, err)
		}
		switch got := string(b[:n]); {
		case got == want:
			return nil
		case got == fmt.Sprintf("no %d", a.seq):
			return fmt.Errorf("the app could not protect socket %d", fd)
		default:
			logf("protect: a late answer %q dropped (waiting for %q)", got, want)
		}
	}
}
