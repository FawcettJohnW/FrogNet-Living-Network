// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// fdf-tun -- [BRIDGE_V1] the member's packet bridge (PLAN-BRIDGE §3.1): one TUN, one WireGuard device per scope, the 1:1
// rewrite between them. A static linux binary: on a phone the app ships it as libfdftun.so and runs it; in the netns
// tests it runs as itself. The planner (Kotlin, Member/Reach) decides; this applies.
//
//	fdf-tun --tun NAME              create TUN NAME here (no virtio header, as Android's is); the caller addresses it
//	fdf-tun --fd-socket NAME        Android: listen on the abstract unix socket @NAME (SOCK_SEQPACKET); the app connects,
//	                                sends its VpnService TUN fd (SCM_RIGHTS); only a peer with this process's uid is taken.
//	                                Every socket WireGuard opens is sent back on it to be protected (VpnService.protect)
//	                                BEFORE it binds ([FDF_PROTECT_HOOK_V1]); the app answers "ok" or the bind fails.
//
// Control: JSON lines on stdin, one answer line each on stdout:
//
//	{"op":"set","local":"10.252.0.1","forward":{...}|null,"islands":[...]}  -> {"ok":true} | {"ok":false,"error":"..."}
//	{"op":"status"}                                                        -> {"ok":true,"status":{...}}
//	{"op":"version"}                                                       -> {"ok":true,"build":"..."}
//
// stdin closed = the member is gone: everything is closed and fdf-tun exits (gone is gone). Log lines go to stderr.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sync/atomic"
)

const build = "fdf-tun-2026-10-11-b11"

func logf(f string, a ...any) {
	fmt.Fprintf(os.Stderr, "fdf-tun: "+f+"\n", a...)
}

func main() {
	tunName := flag.String("tun", "", "create this TUN here (tests)")
	fdSock := flag.String("fd-socket", "", "abstract unix socket on which the app sends its TUN fd (Android)")
	flag.Parse()
	logf("build %s", build)
	t, name, err := openTun(*tunName, *fdSock)
	if err != nil {
		logf("%v", err)
		fmt.Printf(`{"ok":false,"error":%q}`+"\n", err.Error())
		os.Exit(2)
	}
	logf("TUN %s", name)
	e := newEngine(t, logf)
	var closing atomic.Bool // stdin closed: the TUN is closed on purpose, its reader ends quietly
	go func() {             // the TUN's read side: what applications send
		bufs, sizes := [][]byte{make([]byte, 65535)}, []int{0}
		for {
			n, err := t.Read(bufs, sizes, 0)
			if err != nil {
				if closing.Load() {
					return
				}
				logf("TUN read: %v -- exiting (the VPN is gone)", err)
				e.closeAll()
				os.Exit(1)
			}
			for i := 0; i < n; i++ {
				e.fromTun(bufs[i][:sizes[i]])
			}
		}
	}()
	out := bufio.NewWriter(os.Stdout)
	answer := func(v any) {
		b, _ := json.Marshal(v)
		out.Write(append(b, '\n'))
		out.Flush()
	}
	answer(map[string]any{"ok": true, "build": build, "tun": name})
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	for in.Scan() {
		var req struct {
			Op string `json:"op"`
			SetCfg
		}
		if err := json.Unmarshal(in.Bytes(), &req); err != nil {
			answer(map[string]any{"ok": false, "error": "not JSON: " + err.Error()})
			continue
		}
		switch req.Op {
		case "set":
			if err := e.set(req.SetCfg); err != nil {
				logf("set refused: %v", err)
				answer(map[string]any{"ok": false, "error": err.Error()})
			} else {
				answer(map[string]any{"ok": true})
			}
		case "status":
			answer(map[string]any{"ok": true, "status": e.status()})
		case "version":
			answer(map[string]any{"ok": true, "build": build})
		default:
			answer(map[string]any{"ok": false, "error": "unknown op " + req.Op})
		}
	}
	logf("stdin closed: the member is gone, so is every scope")
	closing.Store(true)
	e.closeAll()
	t.Close()
}
