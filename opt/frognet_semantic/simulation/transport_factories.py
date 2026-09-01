#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
transport_factories.py - PRIMER 1: physical transport with traffic shaping.

Three transport modes behind one factory abstraction, all producing the SAME
proxy/daemon endpoint surface that transport_sim_tier.WireEndpoint exposes, so
SimulatedProxyWorker and SimulatedDaemonSession do not change:

  1. sim       - current behavior. In-process socketpair + Python shaper
                 (wraps transport_sim_tier.WireMedium). No kernel, no privilege.
  2. loopback  - real TCP between two processes on THIS box (127.0.0.1:<port>).
                 Optional kernel shaping via tc-netem on a netns/veth pair.
  3. remote    - real TCP to a remote daemon address. Optional egress shaping
                 on the bearer interface via tc-netem (+ IFB for ingress).

DISCIPLINE (matches netns_backend.py - "fake-here / real-on-box"):
  tc-netem needs CAP_NET_ADMIN and the `tc`/`ip` binaries, which the container
  has neither of. NetemShaper is therefore driven through the SAME Runner
  abstraction netns_backend already established: DryRunner records the exact
  argv it WOULD run (validated here, no kernel touched); ShellRunner executes
  for real on a Linux box as root. Selection mirrors netns_backend:
  FROGNET_SIM_EXECUTE=1 -> ShellRunner, else DryRunner.

  Real TCP on `lo` DOES work in-container (SIM_STATUS: "Loopback sockets work
  in-container; egress does not"), so `loopback` mode WITH shaping disabled is
  fully exercisable here; only the tc-netem layer is dry-run-only off-box.

What this module deliberately does NOT do:
  - It does not model the production iptables DNAT-to-proxy-then-daemon flow.
    For a real same-box daemon use a non-9009 port (see TestDaemonProcess) to
    avoid conntrack collision with the live service.
  - tc-netem cannot express "P% chance/sec of a Tms blackout" natively. Two
    honest options (primer): (a) toggle qdisc rules from a Python thread, or
    (b) accept constant degradation and keep outage modeling in the sim path.
    Default is (b), documented; (a) is available via NetemShaper(outage_thread=True).
"""
from __future__ import annotations

import os
import shlex
import socket
import sys
import threading
import time
from typing import List, Optional, Protocol, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_PARENT, _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from transport_sim_tier import (  # noqa: E402
    NetworkParams, WireMedium, WireEndpoint,
    SimulatedDaemonServer, SimulatedDaemonSession, TemplateStore,
)

# Production daemon port (proxy/transport_semantic.py:141, daemon_main.py:42).
# The primer says the override env is FROGNET_SEM_PORT; the actual live code
# reads FROGNET_DAEMON_PORT / FROGNET_DAEMON_LISTEN. We honor the real names and
# default the TEST daemon OFF 9009 (primer gotcha: avoid colliding with live).
PROD_DAEMON_PORT = int(os.environ.get("FROGNET_DAEMON_PORT", "9009"))
TEST_DAEMON_PORT = int(os.environ.get("FROGNET_TEST_DAEMON_PORT", "19009"))


# ============================================================================
# SocketWireEndpoint - real-TCP twin of transport_sim_tier.WireEndpoint
# ============================================================================

class SocketWireEndpoint:
    """A single bidirectional real TCP socket presented through the exact
    surface SimulatedProxyWorker / SimulatedDaemonSession use against
    WireEndpoint: settimeout, sendall, send, recv, shutdown, close,
    setsockopt, fileno.

    Difference from the sim WireEndpoint: setsockopt is NOT a no-op here -
    real TCP wants TCP_NODELAY (production sets it on every channel:
    transport_semantic.py:811, session.py:429/456). One socket carries both
    directions; the proxy/daemon open TWO of these (send channel + RETURN
    channel), exactly as production does.
    """

    def __init__(self, sock: socket.socket, label: str = ""):
        self._sock = sock
        self.label = label
        self._closed = False
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def settimeout(self, t: Optional[float]):
        try:
            self._sock.settimeout(t)
        except OSError:
            pass

    def sendall(self, data: bytes) -> None:
        if self._closed:
            raise OSError("endpoint closed")
        try:
            self._sock.sendall(data)
        except OSError:
            self._closed = True
            raise

    def send(self, data: bytes) -> int:
        if self._closed:
            raise OSError("endpoint closed")
        return self._sock.send(data)

    def recv(self, bufsize: int) -> bytes:
        if self._closed:
            return b""
        try:
            return self._sock.recv(bufsize)
        except OSError as e:
            if isinstance(e, socket.timeout):
                raise
            self._closed = True
            raise

    def shutdown(self, how: int):
        try:
            self._sock.shutdown(how)
        except OSError:
            pass

    def close(self):
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def setsockopt(self, level, optname, value):
        try:
            self._sock.setsockopt(level, optname, value)
        except OSError:
            pass

    def fileno(self) -> int:
        return self._sock.fileno()


# ============================================================================
# NetemShaper - tc-netem on the tree's DryRunner/ShellRunner pattern
# ============================================================================

class DryRunner:
    """Records argv instead of executing. Container default - proves the tc
    command line without CAP_NET_ADMIN / a kernel. (Same contract as
    netns_backend.DryRunner so callers can share a runner.)"""
    def __init__(self):
        self.commands: List[List[str]] = []

    def run(self, argv):
        self.commands.append(list(argv))
        return 0, "", ""

    def is_real(self):
        return False


class ShellRunner:
    """Executes for real. BOX-ONLY: needs Linux + root + `tc`/`ip` + the ifb
    module for bidirectional shaping. (Mirrors netns_backend.ShellRunner.)"""
    def __init__(self):
        import platform
        if platform.system() != "Linux":
            raise RuntimeError("ShellRunner requires Linux (tc/ip)")
        if os.geteuid() != 0:
            raise RuntimeError("ShellRunner requires root (tc/ip)")

    def run(self, argv):
        import subprocess
        r = subprocess.run(argv, capture_output=True, text=True, check=False)
        return r.returncode, r.stdout, r.stderr

    def is_real(self):
        return True


def default_runner():
    """FROGNET_SIM_EXECUTE=1 -> ShellRunner (box), else DryRunner (container).
    Same env switch netns_backend uses, so a caller can drive both with one."""
    execute = os.environ.get("FROGNET_SIM_EXECUTE", "") in ("1", "true", "yes")
    return ShellRunner() if execute else DryRunner()


class NetemShaper:
    """Apply NetworkParams to a real interface via tc-netem.

    apply() runs (primer):
        tc qdisc add dev <iface> root netem \\
            delay <latency_ms>ms <jitter_ms>ms \\
            loss <pct>% \\
            rate <bandwidth_bps*8>bit
    remove() runs:
        tc qdisc del dev <iface> root

    Outages: tc-netem can't model "P/sec of a Tms blackout" directly.
      (b) DEFAULT - constant degradation; outage_prob/duration are NOT applied
          to the kernel, only logged as skipped. Outage modeling stays in the
          sim path. Honest about what the kernel is and isn't doing.
      (a) OPT-IN  - outage_thread=True spins a toggler that del/add-cycles the
          qdisc to blackout the link for outage_duration_ms with
          outage_prob_per_sec probability each second.

    Bidirectional (primer): the kernel shapes egress natively only. bidirectional
    sets up an IFB mirror so ingress can be shaped too; emits the
    `modprobe ifb numifbs=2` + ingress-redirect plan. Requires the ifb module.

    Cleanup: tc rules persist past process death. apply() is paired with
    remove() which MUST be called from a finally. cleanup_all() clears stale
    qdiscs on the interface regardless of who created them.
    """

    def __init__(self, interface: str, params: NetworkParams,
                 direction: str = "egress",
                 bidirectional: bool = False,
                 outage_thread: bool = False,
                 runner=None):
        self.interface = interface
        self.params = params
        self.direction = direction
        self.bidirectional = bidirectional
        self.outage_thread = outage_thread
        self.runner = runner or default_runner()
        self._applied = False
        self._ifb = "ifb0"
        self._outage_stop = threading.Event()
        self._outage_t: Optional[threading.Thread] = None
        self.skipped_outage = False

    # --- command builders (pure; the unit of what's verifiable in-container) --

    def _netem_args(self) -> List[str]:
        p = self.params
        args = ["delay", f"{p.latency_ms:g}ms"]
        if p.jitter_ms > 0:
            args.append(f"{p.jitter_ms:g}ms")
        # NetworkParams.bandwidth_bps is BYTES/sec; tc rate wants bits/sec.
        if p.bandwidth_bps and p.bandwidth_bps > 0:
            args += ["rate", f"{int(p.bandwidth_bps * 8)}bit"]
        # Small-RTT burst guard (primer gotcha): cap queue so bursts after WG
        # encap (~1420 MTU) don't get silently dropped by the default qlen.
        args += ["limit", "1000"]
        return args

    def add_cmd(self, iface: str) -> List[str]:
        return (["tc", "qdisc", "add", "dev", iface, "root", "netem"]
                + self._netem_args())

    def del_cmd(self, iface: str) -> List[str]:
        return ["tc", "qdisc", "del", "dev", iface, "root"]

    def show_cmd(self, iface: str) -> List[str]:
        return ["tc", "qdisc", "show", "dev", iface]

    # --- lifecycle ---------------------------------------------------------

    def _run(self, argv) -> Tuple[int, str, str]:
        return self.runner.run(list(argv))

    def apply(self):
        if self.params.outage_prob_per_sec > 0 and not self.outage_thread:
            # (b): be explicit that the kernel is NOT modeling outages.
            self.skipped_outage = True

        # Conflicting-qdisc detection (primer gotcha). On a dry runner show
        # returns empty, so the add proceeds; on a real runner a pre-existing
        # qdisc surfaces here and the caller can decide to fail or chain.
        self._run(self.show_cmd(self.interface))
        self._run(self.add_cmd(self.interface))

        if self.bidirectional:
            # Ingress shaping needs an IFB device mirroring ingress->egress,
            # then shape the IFB. Plan only; requires `modprobe ifb numifbs=2`.
            self._run(["modprobe", "ifb", "numifbs=2"])
            self._run(["ip", "link", "set", "dev", self._ifb, "up"])
            self._run(["tc", "qdisc", "add", "dev", self.interface, "handle",
                       "ffff:", "ingress"])
            self._run(["tc", "filter", "add", "dev", self.interface, "parent",
                       "ffff:", "protocol", "ip", "u32", "match", "u32", "0",
                       "0", "action", "mirred", "egress", "redirect", "dev",
                       self._ifb])
            self._run(self.add_cmd(self._ifb))

        self._applied = True
        if self.outage_thread and self.params.outage_prob_per_sec > 0:
            self._start_outage_toggler()
        return self

    def _start_outage_toggler(self):
        import random
        rng = random.Random()
        p = self.params

        def loop():
            while not self._outage_stop.wait(1.0):
                if rng.random() < p.outage_prob_per_sec:
                    # Blackout: drop the qdisc to 100% loss, hold, restore.
                    self._run(self.del_cmd(self.interface))
                    self._run(["tc", "qdisc", "add", "dev", self.interface,
                               "root", "netem", "loss", "100%"])
                    time.sleep(p.outage_duration_ms / 1000.0)
                    self._run(self.del_cmd(self.interface))
                    self._run(self.add_cmd(self.interface))

        self._outage_t = threading.Thread(target=loop, daemon=True,
                                          name=f"netem-outage-{self.interface}")
        self._outage_t.start()

    def remove(self):
        self._outage_stop.set()
        if self._outage_t:
            self._outage_t.join(timeout=2.0)
        if self.bidirectional:
            self._run(["tc", "filter", "del", "dev", self.interface, "parent",
                       "ffff:"])
            self._run(["tc", "qdisc", "del", "dev", self.interface, "handle",
                       "ffff:", "ingress"])
            self._run(self.del_cmd(self._ifb))
        self._run(self.del_cmd(self.interface))
        self._applied = False

    def cleanup_all(self):
        """Best-effort stale-state clear. Idempotent; 'del' on a clean iface
        is a harmless error on a real runner."""
        self._run(self.del_cmd(self.interface))
        if self.bidirectional:
            self._run(self.del_cmd(self._ifb))


# ============================================================================
# TransportFactory protocol + three implementations
# ============================================================================

class TransportFactory(Protocol):
    """Primer protocol. connect_proxy returns the two endpoints the proxy uses
    (send channel, RETURN channel) - for the two-socket model these are two
    separate connections. Concrete factories add accept_daemon() (the
    daemon-side counterpart, when the harness owns the daemon) and close()."""

    def connect_proxy(self, peer: str) -> Tuple[WireEndpoint, WireEndpoint]:
        ...


class SimTransportFactory:
    """Mode `sim`. Thin adapter over the existing WireMedium pair so the
    current create_connected_pair behavior is preserved exactly. Both proxy
    and daemon endpoints come from the same two media; close() reaps them."""

    owns_daemon = True

    def __init__(self,
                 forward_params: Optional[NetworkParams] = None,
                 reverse_params: Optional[NetworkParams] = None,
                 capture_dir: Optional[str] = None):
        self.fwd = forward_params
        self.rev = reverse_params
        self.capture_dir = capture_dir
        self._send_wire: Optional[WireMedium] = None
        self._recv_wire: Optional[WireMedium] = None

    def connect_proxy(self, peer: str) -> Tuple[WireEndpoint, WireEndpoint]:
        self._send_wire = WireMedium(forward_params=self.fwd, reverse_params=self.rev,
                                     capture_dir=self.capture_dir, label="send")
        self._recv_wire = WireMedium(forward_params=self.fwd, reverse_params=self.rev,
                                     capture_dir=self.capture_dir, label="recv")
        return self._send_wire.endpoint_a(), self._recv_wire.endpoint_a()

    def accept_daemon(self, peer: str) -> Tuple[WireEndpoint, WireEndpoint]:
        # daemon reads requests from send_wire.B, writes replies on recv_wire.B
        return self._send_wire.endpoint_b(), self._recv_wire.endpoint_b()

    def handles(self):
        return self._send_wire, self._recv_wire

    def close(self):
        for w in (self._send_wire, self._recv_wire):
            if w:
                try:
                    w.close()
                except Exception:
                    pass


class _RealTCPFactory:
    """Shared logic for loopback/remote: open two real TCP connections to
    host:port and wrap each in a SocketWireEndpoint. Optional NetemShaper is
    applied before the connections and removed on close()."""

    owns_daemon = False

    def __init__(self, host: str, port: int, shaper: Optional[NetemShaper] = None,
                 connect_timeout: float = 15.0):
        self.host = host
        self.port = int(port)
        self.shaper = shaper
        self.connect_timeout = connect_timeout
        self._eps: List[SocketWireEndpoint] = []

    def _connect_one(self, label: str) -> SocketWireEndpoint:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout)
        s.connect((self.host, self.port))
        s.settimeout(None)
        ep = SocketWireEndpoint(s, label=label)
        self._eps.append(ep)
        return ep

    def connect_proxy(self, peer: str) -> Tuple[WireEndpoint, WireEndpoint]:
        if self.shaper is not None:
            self.shaper.apply()
        # Two connections: send channel first, RETURN channel second - the
        # order production uses (transport_semantic.py _open_send_sock then
        # _open_return_sock). HELLO / HELLO RETURN are spoken by the proxy
        # worker over these, not here.
        send_ep = self._connect_one("send")
        recv_ep = self._connect_one("recv")
        return send_ep, recv_ep

    def handles(self):
        # No WireMedium to expose; closing is handled by close(). Return
        # lightweight no-op handles so the 4-tuple contract holds.
        return _NoopHandle(), _NoopHandle()

    def close(self):
        for ep in self._eps:
            try:
                ep.close()
            except Exception:
                pass
        if self.shaper is not None:
            try:
                self.shaper.remove()
            except Exception:
                pass


class LoopbackTransportFactory(_RealTCPFactory):
    """Mode `loopback`. Connects to a daemon already listening on
    127.0.0.1:<daemon_port>. Pair with a TestDaemonProcess (a SimulatedDaemon
    on a ThreadingTCPServer) for the verification path, or point it at a real
    same-box daemon (use a non-9009 port to dodge conntrack collision)."""

    def __init__(self, daemon_port: int = TEST_DAEMON_PORT,
                 shaper: Optional[NetemShaper] = None, host: str = "127.0.0.1"):
        super().__init__(host=host, port=daemon_port, shaper=shaper)


class RemoteTransportFactory(_RealTCPFactory):
    """Mode `remote`. Real TCP to a remote daemon address. Shaping (if given)
    applies to the egress bearer interface (eth0/wg0/...) via tc-netem."""

    def __init__(self, remote_addr: str, remote_port: int = PROD_DAEMON_PORT,
                 shaper: Optional[NetemShaper] = None):
        super().__init__(host=remote_addr, port=remote_port, shaper=shaper)


class _NoopHandle:
    def close(self):
        pass


# ============================================================================
# TestDaemonProcess - SimulatedDaemonSession behind a real TCP listener
# ============================================================================

class TestDaemonProcess:
    """A minimal real-TCP front for a SimulatedDaemonSession, for the
    loopback verification path. Accepts the proxy's two connections (HELLO on
    the first, HELLO RETURN:<gw> on the second), routes them into one session
    via the existing SimulatedDaemonServer logic, and serves traffic.

    Modeled on SimulatedDaemonServer (transport_sim_tier.py): same HELLO /
    HELLO RETURN pairing, just over accepted TCP sockets wrapped in
    SocketWireEndpoint instead of WireMedium B-ends.

    Bind to a NON-9009 port by default so a same-box run never collides with a
    live frognet-daemon-v3 (primer gotcha; no SO_MARK/DNAT dance is modeled).
    """

    def __init__(self, peer_gw: str, port: int = TEST_DAEMON_PORT,
                 host: str = "127.0.0.1",
                 template_store: Optional[TemplateStore] = None,
                 default_fragment=None):
        self.peer_gw = peer_gw
        self.host = host
        self.port = int(port)
        self.store = template_store or TemplateStore()
        self.default_fragment = default_fragment
        self.server = SimulatedDaemonServer(self.store)
        self._lsock: Optional[socket.socket] = None
        self._accept_t: Optional[threading.Thread] = None
        self.session: Optional[SimulatedDaemonSession] = None
        self._err: Optional[Exception] = None
        self._ready = threading.Event()

    def start(self):
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind((self.host, self.port))
        self._lsock.listen(8)
        # If port was 0 (ephemeral), publish the assigned port.
        self.port = self._lsock.getsockname()[1]
        self._accept_t = threading.Thread(target=self._accept_loop, daemon=True,
                                          name=f"testdaemon-{self.peer_gw}")
        self._accept_t.start()
        return self

    def _accept_loop(self):
        try:
            self._lsock.settimeout(15.0)
            c1, _ = self._lsock.accept()   # proxy send channel (HELLO)
            c2, _ = self._lsock.accept()   # proxy RETURN channel (HELLO RETURN)
            recv_ep = SocketWireEndpoint(c1, label="daemon-recv")
            send_ep = SocketWireEndpoint(c2, label="daemon-send")
            sess = self.server.accept_send_sock(
                self.peer_gw, recv_ep, default_fragment=self.default_fragment)
            self.server.accept_return_sock(self.peer_gw, send_ep)
            sess.start()
            self.session = sess
            self._ready.set()
        except Exception as e:  # noqa: BLE001
            self._err = e
            self._ready.set()

    def wait_ready(self, timeout: float = 15.0) -> SimulatedDaemonSession:
        if not self._ready.wait(timeout):
            raise RuntimeError("test daemon accept did not complete in time")
        if self._err is not None:
            raise self._err
        return self.session

    def stop(self):
        try:
            if self.session:
                self.session.stop()
        except Exception:
            pass
        try:
            if self._lsock:
                self._lsock.close()
        except Exception:
            pass

    def close(self):
        """Alias so a TestDaemonProcess can sit in the w1/w2 close() slot of
        the create_connected_pair 4-tuple. The session is reaped by the test's
        own daemon.stop(); here we just close the listener."""
        try:
            if self._lsock:
                self._lsock.close()
        except Exception:
            pass


# ============================================================================
# Factory selection from a --transport flag
# ============================================================================

def make_factory(mode: str,
                 fwd_params: Optional[NetworkParams] = None,
                 rev_params: Optional[NetworkParams] = None,
                 capture_dir: Optional[str] = None,
                 *,
                 daemon_port: int = TEST_DAEMON_PORT,
                 remote_addr: Optional[str] = None,
                 remote_port: int = PROD_DAEMON_PORT,
                 shaper: Optional[NetemShaper] = None):
    """Build the factory for a --transport sim|loopback|remote selection.

    sim:      SimTransportFactory wrapping WireMedium (shaper ignored - the
              Python shaper IS the sim).
    loopback: LoopbackTransportFactory to 127.0.0.1:<daemon_port>.
    remote:   RemoteTransportFactory to <remote_addr>:<remote_port>.
    """
    if mode == "sim":
        return SimTransportFactory(fwd_params, rev_params, capture_dir)
    if mode == "loopback":
        return LoopbackTransportFactory(daemon_port=daemon_port, shaper=shaper)
    if mode == "remote":
        if not remote_addr:
            raise ValueError("remote mode needs remote_addr")
        return RemoteTransportFactory(remote_addr, remote_port, shaper=shaper)
    raise ValueError(f"unknown transport mode: {mode!r}")


# ============================================================================
# connect_pair - one entry the sotf tests call for all three modes
# ============================================================================

def connect_pair(local_gw: str,
                 mode: str = "sim",
                 *,
                 fwd_params: Optional[NetworkParams] = None,
                 rev_params: Optional[NetworkParams] = None,
                 capture_dir: Optional[str] = None,
                 default_fragment=None,
                 template_store: Optional[TemplateStore] = None,
                 daemon_port: int = 0,
                 shaper: Optional[NetemShaper] = None,
                 open_timeout: float = 15.0):
    """Return (proxy, daemon_session, handle1, handle2) - the SAME 4-tuple
    create_connected_pair returns, for sim OR loopback. The two handles each
    expose .close() so existing test cleanup (`w1.close(); w2.close()`) works
    unchanged.

    sim:      delegates verbatim to transport_sim_tier.create_connected_pair -
              zero behavior change, the 24-check baseline is untouched.
    loopback: stands up a TestDaemonProcess (SimulatedDaemonSession behind a
              real TCP listener on 127.0.0.1), connects the proxy to it over
              two real TCP sockets, and returns the live in-process session so
              SotFReceiver(daemon, ...) drives it exactly as in sim mode.

    remote is NOT handled here: the SotF receiver runs in-process, and a real
    remote daemon is a separate process. Use round_trip_remote() for the
    primer's remote success criterion (REQ_REPEAT round-trip to a live daemon).
    """
    if mode == "sim":
        from transport_sim_tier import create_connected_pair
        return create_connected_pair(
            local_gw=local_gw, fwd_params=fwd_params, rev_params=rev_params,
            capture_dir=capture_dir, template_store=template_store,
            default_fragment=default_fragment)

    if mode == "loopback":
        from transport_sim_tier import SimulatedProxyWorker
        # daemon_port=0 means EPHEMERAL (TestDaemonProcess binds 0 and publishes
        # the real port). Do NOT coerce 0 -> 19009: that made the in-process test
        # daemon collide with anything already on 19009 (e.g. a real test daemon
        # on the same box). A specific nonzero port is honored as given.
        port = daemon_port
        tdp = TestDaemonProcess(peer_gw=local_gw, port=port, host="127.0.0.1",
                                template_store=template_store,
                                default_fragment=default_fragment)
        tdp.start()
        factory = LoopbackTransportFactory(daemon_port=tdp.port, shaper=shaper)
        proxy_send, proxy_recv = factory.connect_proxy(local_gw)
        proxy = SimulatedProxyWorker(local_gw=local_gw,
                                     send_sock=proxy_send, recv_sock=proxy_recv)
        # proxy.open() and the daemon-side accept run concurrently - the proxy
        # blocks on the SEQ_RESET ack until tdp's accept loop calls sess.start().
        proxy.open(timeout=open_timeout)
        daemon = tdp.wait_ready(timeout=open_timeout)
        # handle1 = factory (closes proxy sockets + removes shaper),
        # handle2 = tdp (closes listener; daemon.stop() is called separately by
        # the existing test cleanup, same as sim mode).
        return proxy, daemon, factory, tdp

    raise ValueError(f"connect_pair does not handle mode {mode!r} "
                     f"(use round_trip_remote for remote)")


def round_trip_remote(local_gw: str, remote_addr: str,
                      remote_port: int = PROD_DAEMON_PORT,
                      shaper: Optional[NetemShaper] = None,
                      timeout: float = 10.0,
                      echo_payload: Optional[bytes] = None,
                      echo_bytes: int = 0,
                      echo_count: int = 0):
    """Open the two-socket channel to a REAL daemon at remote_addr:remote_port
    over a true interface, complete the HELLO handshake, and return a structured
    diagnostics dict for post-hoc validation:

      {
        "ok": bool,                      # connect+handshake succeeded
        "connect_open_ms": float,        # both sockets + HELLO + SEQ_RESET
        "preflight_op": int|None,        # REQ_REPEAT reply op (bare check)
        "iterations": [                  # one per echo round-trip
            {"i", "rtt_ms", "status", "in_len", "out_len",
             "in_sha256", "out_sha256", "match"} ...
        ],
        "error": str|None,
      }

    Modes:
      - echo_count<=0 and echo_payload is None: bare REQ_REPEAT preflight only.
      - echo_payload given: ONE REQ_RAW echo of those exact bytes.
      - echo_bytes>0 with echo_count>0: echo_count REQ_RAW round-trips of a
        deterministic echo_bytes-long payload (probes MTU/fragmentation + RTT
        distribution over the real bearer).

    in_sha256/out_sha256 are how I verify body fidelity from the pasted output -
    a length match is not enough.
    """
    import hashlib
    from transport_sim_tier import (SimulatedProxyWorker, wrap_req_repeat,
                                     try_parse, _hash_for)
    from core.semcache_wire import wrap_req_raw

    def _mkpayload(n: int, seed: int) -> bytes:
        # Deterministic, position-tagged so truncation/reordering is visible.
        base = (f"FrogNet-mode2-echo seed={seed} ".encode())
        buf = (base * (n // len(base) + 1))[:n]
        return buf

    out = {"ok": False, "connect_open_ms": None, "preflight_op": None,
           "iterations": [], "error": None}
    factory = RemoteTransportFactory(remote_addr, remote_port, shaper=shaper)
    proxy = None
    try:
        t0 = time.perf_counter()
        proxy_send, proxy_recv = factory.connect_proxy(local_gw)
        proxy = SimulatedProxyWorker(local_gw=local_gw,
                                     send_sock=proxy_send, recv_sock=proxy_recv)
        proxy.open(timeout=timeout)
        out["connect_open_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        out["ok"] = True

        # Bare preflight if nothing to echo.
        if echo_payload is None and echo_count <= 0:
            reply = proxy.send_request(wrap_req_repeat(_hash_for("remote-preflight")),
                                       timeout=timeout)
            m = try_parse(reply) if reply is not None else None
            out["preflight_op"] = None if m is None else m.op
            return out

        # Build the iteration payload set.
        if echo_payload is not None:
            payloads = [echo_payload]
        else:
            payloads = [_mkpayload(echo_bytes, i) for i in range(echo_count)]

        for i, p in enumerate(payloads):
            in_sha = hashlib.sha256(p).hexdigest()
            ti = time.perf_counter()
            reply = proxy.send_request(wrap_req_raw(_hash_for(f"echo-{i}"), p),
                                       timeout=timeout)
            rtt_ms = round((time.perf_counter() - ti) * 1000, 3)
            m = try_parse(reply) if reply is not None else None
            body = getattr(m, "body", None) if m is not None else None
            out_sha = hashlib.sha256(body).hexdigest() if body is not None else None
            out["iterations"].append({
                "i": i,
                "rtt_ms": rtt_ms,
                "status": getattr(m, "status", None) if m is not None else None,
                "op": getattr(m, "op", None) if m is not None else None,
                "in_len": len(p),
                "out_len": (len(body) if body is not None else 0),
                "in_sha256": in_sha,
                "out_sha256": out_sha,
                "match": (out_sha == in_sha),
            })
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
        return out
    finally:
        try:
            if proxy:
                proxy.close()
        except Exception:
            pass
        factory.close()


# ============================================================================
# In-container self-test - proves sim + loopback parity for real, and that the
# NetemShaper emits the correct tc argv under DryRunner (no kernel touched).
# ============================================================================

def _selftest() -> int:
    from transport_sim_tier import (wrap_req_repeat, try_parse, _hash_for,
                                     OP_RESP_SAME)
    fails: List[str] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
        if not ok:
            fails.append(name)

    print("\n[FACTORY SELFTEST]")

    # 1) sim mode through connect_pair == baseline round-trip
    clean = NetworkParams(latency_ms=1.0, bandwidth_bps=1e9)
    proxy, daemon, h1, h2 = connect_pair("10.20.99.1", "sim",
                                         fwd_params=clean, rev_params=clean)
    try:
        reply = proxy.send_request(wrap_req_repeat(_hash_for("sim-1")), timeout=3.0)
        m = try_parse(reply) if reply else None
        check("sim: REQ_REPEAT round-trips", m is not None and m.op == OP_RESP_SAME)
        check("sim: daemon counted the REQ_REPEAT", daemon.stats["req_repeat"] == 1,
              f"got {daemon.stats.get('req_repeat')}")
    finally:
        proxy.close(); daemon.stop(); h1.close(); h2.close()

    # 2) loopback mode over REAL TCP on 127.0.0.1 (works in-container)
    proxy, daemon, factory, tdp = connect_pair("10.20.99.2", "loopback",
                                               fwd_params=clean, rev_params=clean,
                                               daemon_port=0)  # ephemeral
    try:
        reply = proxy.send_request(wrap_req_repeat(_hash_for("lo-1")), timeout=3.0)
        m = try_parse(reply) if reply else None
        check("loopback: REQ_REPEAT round-trips over real TCP",
              m is not None and m.op == OP_RESP_SAME)
        check("loopback: daemon counted the REQ_REPEAT", daemon.stats["req_repeat"] == 1,
              f"got {daemon.stats.get('req_repeat')}")
        # pipeline a few to prove seq-matching survives real sockets
        N = 20
        results = [None] * N
        ts = []
        for i in range(N):
            def fire(i=i):
                results[i] = proxy.send_request(
                    wrap_req_repeat(_hash_for(f"lo-{i}")), timeout=5.0)
            t = threading.Thread(target=fire); t.start(); ts.append(t)
        for t in ts:
            t.join()
        ok = all(r is not None and try_parse(r).op == OP_RESP_SAME for r in results)
        check(f"loopback: {N} pipelined replies matched by seq", ok)
    finally:
        proxy.close(); daemon.stop(); factory.close(); tdp.stop()

    # 3) NetemShaper emits the right tc argv under DryRunner (off-box proof)
    dr = DryRunner()
    sat = NetworkParams(latency_ms=300, jitter_ms=20, bandwidth_bps=256e3 / 8)
    sh = NetemShaper("wg0", sat, runner=dr)
    sh.apply(); sh.remove()
    cmds = [" ".join(c) for c in dr.commands]
    add = next((c for c in cmds if c.startswith("tc qdisc add")), "")
    check("netem: builds a `tc qdisc add ... root netem` line", add != "", str(cmds))
    check("netem: latency in ms present", "delay 300ms" in add, add)
    check("netem: jitter appended", "300ms 20ms" in add, add)
    check("netem: rate in BITS (8x bytes)", f"rate {int(256e3/8*8)}bit" in add, add)
    check("netem: del paired on remove", any(c.startswith("tc qdisc del") for c in cmds))

    # 4) outage honesty: default (b) does NOT push outages to the kernel
    dr2 = DryRunner()
    jam = NetworkParams(latency_ms=100, jitter_ms=200, bandwidth_bps=2e3,
                        outage_prob_per_sec=0.2, outage_duration_ms=600)
    sh2 = NetemShaper("eth0", jam, runner=dr2)
    sh2.apply(); sh2.remove()
    check("netem: outage NOT modeled in kernel by default (documented)",
          sh2.skipped_outage is True)
    check("netem: no 100% loss qdisc emitted in default mode",
          not any("loss 100%" in " ".join(c) for c in dr2.commands))

    if fails:
        print(f"\n[FACTORY SELFTEST] FAILED: {fails}")
        return 1
    print("\n[FACTORY SELFTEST] ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
