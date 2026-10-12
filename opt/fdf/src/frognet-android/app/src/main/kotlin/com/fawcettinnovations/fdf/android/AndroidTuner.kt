// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// AndroidTuner.kt -- [KEEPALIVE_5S_V1] on Android: TCP_NODELAY, SO_KEEPALIVE, TCP_KEEPIDLE 2, TCP_KEEPINTVL 1,
// TCP_KEEPCNT 3 (2 + 3 x 1 = 5 s idle), TCP_USER_TIMEOUT 5000 ms (5 s with data unacknowledged): the values both C++ ends
// set (frogram.cpp dial, ram_server.cpp serve). java.net.Socket on Android has
// no per-socket keepalive timing, so they are set on the socket's descriptor with Os.setsockoptInt (API 21). The three
// TCP_KEEP* constants are not in android.system.OsConstants; their Linux values are fixed by the kernel ABI
// (include/uapi/linux/tcp.h: TCP_KEEPIDLE 4, TCP_KEEPINTVL 5, TCP_KEEPCNT 6, TCP_USER_TIMEOUT 18). Without them a silent death of the
// region would be seen after the OS default (2 h). A failure to set them is an error, not a quieter socket.
package com.fawcettinnovations.fdf.android

import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import com.fawcettinnovations.fdf.member.SocketTuner

object AndroidTuner : SocketTuner {
    private const val TCP_KEEPIDLE = 4
    private const val TCP_KEEPINTVL = 5
    private const val TCP_KEEPCNT = 6
    private const val TCP_USER_TIMEOUT = 18
    override fun tune(s: java.net.Socket) {
        s.tcpNoDelay = true
        s.keepAlive = true
        s.sendBufferSize = 32 * 1024                               // THE BUFFER IS THE LATENCY (frogram.cpp)
        ParcelFileDescriptor.fromSocket(s).use { pfd ->            // a dup of the same socket: the options are the socket's
            val fd = pfd.fileDescriptor
            Os.setsockoptInt(fd, OsConstants.SOL_SOCKET, OsConstants.SO_KEEPALIVE, 1)
            Os.setsockoptInt(fd, OsConstants.IPPROTO_TCP, TCP_KEEPIDLE, 2)
            Os.setsockoptInt(fd, OsConstants.IPPROTO_TCP, TCP_KEEPINTVL, 1)
            Os.setsockoptInt(fd, OsConstants.IPPROTO_TCP, TCP_KEEPCNT, 3)
            Os.setsockoptInt(fd, OsConstants.IPPROTO_TCP, TCP_USER_TIMEOUT, 5000)
        }
    }
}
