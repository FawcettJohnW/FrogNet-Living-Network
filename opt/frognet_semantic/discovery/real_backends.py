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
real_backends.py - production implementations of the injected discovery edges,
written to the EXACT contracts read from sync_interfaces.sh:

  echo_probe   : curl -sS --connect-timeout 2 --max-time T -H "Host: $pip"
                 http://$pip/frognet_echo.php ; 3 tries; validate CSV regex
                 ^[A-Za-z0-9._-]+,10\\.\\d+\\.\\d+\\.\\d+,[0-9.]*,[0-9.]*$
  measure_rtt  : frognet_alive.bash $pip 3 -> "<ms>|<method>"; ms before '|',
                 before '.'; "0" -> 1; empty on failure
  get_hosts    : curl -sS -o F -w %{http_code} -H "Host: $host_path"
                 http://$pip/getHosts.php ; 3 tries until 200; jq '.[]?.ip'
  broker       : handshake_rtts.json entry where value.peer_dot_one==ip ->
                 (channel_key, value.subnet, value.peer_dot_one)
  channel_iface: active/*.json where .interface==dev -> .channel_name
  ping         : ping -I dev -c1 -W2 via

Parse logic is factored into pure helpers (tested without network). The network/
subprocess methods themselves are UNVALIDATED in this environment (no box) and
are flagged for on-box verification.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import time

from frognet_log import get_logger
_LOG = get_logger("discovery.real_backends")

ECHO_RE = re.compile(r"^[A-Za-z0-9._-]+,10\.[0-9]+\.[0-9]+\.[0-9]+,[0-9.]*,[0-9.]*$")


# ---- pure parse helpers (network-free, unit-tested) ----------------------

def valid_echo(cand: str) -> bool:
    cand = (cand or "").replace("\\n", "").replace("\r", "").replace("\n", "")
    return bool(ECHO_RE.match(cand))


def parse_rtt(result: str) -> str:
    """frognet_alive '<ms>|<method>' -> ms (int str); '0'->'1'; '' on failure."""
    if not result:
        return ""
    ms = result.split("|", 1)[0].split(".", 1)[0]
    if not ms:
        return ""
    return "1" if ms == "0" else ms


def parse_gethosts(json_text: str) -> list:
    """getHosts.php JSON -> [(ip, name), ...] (name "" if absent). The name is
    needed to record vouched hosts in /etc/hosts without a direct echo."""
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    out = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("ip"):
                out.append((item["ip"], item.get("name", "")))
    return out


def broker_lookup(handshake: dict, ip: str):
    """handshake_rtts mapping -> (channel, subnet, peer_dot_one) | None."""
    for ch, v in handshake.items():
        if isinstance(v, dict) and v.get("peer_dot_one") == ip:
            return (ch, v.get("subnet", ""), v.get("peer_dot_one"))
    return None


# ---- production backends -------------------------------------------------

class RealEcho:
    def __init__(self, curl="/usr/bin/curl", echo_timeout=None):
        self.curl = curl
        # bash spec: ECHO_TIMEOUT default 15 (FROGNET_DISCOVERY_ECHO_TIMEOUT).
        # The port had lowered this to 5 "for speed"; 5s cannot absorb a cold
        # semantic-proxy first contact (REQ_FULL template build ~2s+, worse on a
        # bad cold run), so the curl aborts at 5s, the in-flight RPC is cut and
        # logged as a no-reply fail -> false FAIL_ECHO -> host culled -> the
        # merge never reaches a clean run. Restored to the bash spec value, 15.
        self.t = int(echo_timeout if echo_timeout is not None
                     else os.environ.get("FROGNET_ECHO_TIMEOUT", "15"))

    def _try(self, url, pip):
        # [ECHO_SOMARK_DIRECT_V1] Discovery echo must go DIRECT to the neighbour,
        # not through the semantic proxy. A plain curl to tcp/80 is trapped by the
        # OUTPUT REDIRECT into the semantic RPC - which, for an on-segment LAN
        # neighbour (e.g. Seattle3 at 10.250.250.191, reached on eth0 with no peer
        # session), has no in-flight to coalesce onto and hangs to the 60s safety
        # cap, stalling the whole merge. SO_MARK=1 makes the iptables rule RETURN
        # (bypass the redirect), so the request reaches the neighbour's real port 80
        # directly on the discovery plane - exactly what a manual curl from the box
        # does. Mirrors the reflect handler's MarkedHTTPConnection use.
        try:
            from proxy.transport_real import MarkedHTTPConnection
        except Exception:
            return self._try_curl(url, pip)
        try:
            conn = MarkedHTTPConnection(pip, 80, timeout=float(self.t))
            conn.request("GET", "/frognet_echo.php",
                         headers={"Host": pip, "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", "replace")
            conn.close()
        except Exception:
            return None
        cand = (body or "").replace("\r", "").replace("\n", "")
        return cand if valid_echo(cand) else None

    def _try_curl(self, url, pip):
        # Fallback if the marked-connection path is unavailable (should not happen
        # on a real node; kept so a degraded import never blocks discovery).
        r = subprocess.run(
            [self.curl, "-fsS", "--connect-timeout", "2", "--max-time", str(self.t),
             "-H", f"Host: {pip}", url],
            capture_output=True, text=True)
        cand = (r.stdout or "").replace("\r", "").replace("\n", "")
        return cand if valid_echo(cand) else None

    def echo_probe(self, pip):  # [ECHO_PORT_80_ONLY] FrogNet echo is port 80 - NEVER :8080
        cand = self._try(f"http://{pip}/frognet_echo.php", pip)
        return cand if cand else None


class RealRtt:
    def __init__(self, alive="/usr/local/bin/frognet_alive.bash"):
        self.alive = alive

    def measure_rtt(self, dev, pip):  # dev unused (bash: frognet_alive $pip 3)
        r = subprocess.run([self.alive, pip, "3"], capture_output=True, text=True)
        return parse_rtt(r.stdout.strip())


class RealGetHosts:
    def __init__(self, curl="/usr/bin/curl", timeout=5):
        self.curl, self.t = curl, timeout

    def get_hosts(self, host_path, pip):  # UNVALIDATED on-box
        # [GETHOSTS_SOMARK_DIRECT_V1] Like the echo, the getHosts crawl must go
        # DIRECT (SO_MARK=1) to the neighbour's real port 80, not through the
        # semantic-proxy REDIRECT. Trapped into the semantic RPC it hangs / returns
        # the wrong host's data for on-segment LAN neighbours. Bypass the redirect.
        try:
            from proxy.transport_real import MarkedHTTPConnection
        except Exception:
            return self._get_hosts_curl(host_path, pip)
        for _ in range(3):
            try:
                conn = MarkedHTTPConnection(pip, 80, timeout=float(self.t))
                conn.request("GET", "/getHosts.php",
                             headers={"Host": host_path, "Connection": "close"})
                resp = conn.getresponse()
                code = resp.status
                body = resp.read().decode("utf-8", "replace")
                conn.close()
            except Exception:
                continue
            if code == 200:
                return parse_gethosts(body)
        return []

    def _get_hosts_curl(self, host_path, pip):
        for _ in range(3):
            r = subprocess.run(
                [self.curl, "-sS", "-w", "%{http_code}", "--connect-timeout", "2",
                 "--max-time", str(self.t), "-H", f"Host: {host_path}",
                 f"http://{pip}/getHosts.php"], capture_output=True, text=True)
            body, code = r.stdout[:-3], r.stdout[-3:]
            if code == "200":
                return parse_gethosts(body)
        return []


class RealBroker:
    def __init__(self, handshake_path="/var/lib/frognet-tunnel/handshake_rtts.json",
                 active_dir="/var/lib/frognet-tunnel/active",
                 transit_map_path="/var/lib/frognet-tunnel/node_transit.json"):
        self.handshake_path = handshake_path
        self.active_dir = active_dir
        self.transit_map_path = transit_map_path

    def _handshake(self) -> dict:
        try:
            with open(self.handshake_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def broker_for_peer_ip(self, dest1):
        return broker_lookup(self._handshake(), dest1)

    def channel_for_iface(self, dev):
        try:
            files = sorted(os.listdir(self.active_dir))
        except OSError:
            return ""
        for name in files:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.active_dir, name)) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if d.get("interface") == dev and d.get("channel_name"):
                return d["channel_name"]
        return ""

    def transits(self, relay_one, dest24):
        # [VOUCH_TRANSIT_GATE_V1] relay /24-prefix -> reachable /24 CIDRs, written
        # each poll by the tunnel daemon from the broker's /api/v4/transit-map.
        # Map absent/unparseable -> inert (True): gate behaves like the pre-gate
        # build until the data is live. Relay present -> dest must be in its set.
        # Relay absent (unregistered LAN leaf, e.g. New-York-2) -> not a legit
        # transit for a remote /24 -> suppress.
        try:
            with open(self.transit_map_path) as f:
                m = json.load(f)
        except (OSError, ValueError):
            return True
        if not isinstance(m, dict) or not m:
            return True
        prefix = relay_one.rsplit(".", 1)[0]
        if prefix in m:
            return dest24 in (m[prefix] or [])
        return False


class RealHealthEcho:
    """Health-check echo: returns (http_code, body) for run_tunnel_health_check,
    via the same curl transport as RealEcho. UNVALIDATED on-box."""
    def __init__(self, curl="/usr/bin/curl", echo_timeout=5):
        self.curl, self.t = curl, echo_timeout

    def __call__(self, peer_ip, iface=""):
        # [HEALTH_PROBE_SRC_V1 REVERTED 2026-06-06] Adding --interface here
        # regressed tunnels that were previously TUNNEL_HEALTHY (wg1 carried
        # BAMacBook traffic, went code=000 the moment --interface was added).
        # Back to the bare curl that had wg1/wg2 healthy; pinning is reverted
        # pending on-box isolation of which of {--interface, src} broke it.
        # iface accepted for signature compatibility but intentionally unused.
        r = subprocess.run(
            [self.curl, "-sS", "-w", "%{http_code}", "--connect-timeout", "2",
             "--max-time", str(self.t), "-H", f"Host: {peer_ip}",
             f"http://{peer_ip}/frognet_echo.php"], capture_output=True, text=True)
        out = r.stdout or ""
        return (out[-3:], out[:-3]) if len(out) >= 3 else ("", "")


class RealPing:
    def __init__(self, ping="/bin/ping", count=1, wait=2):
        self.ping, self.count, self.wait = ping, count, wait

    def __call__(self, via, dev):  # ping -I dev -c1 -W2 via
        r = subprocess.run(
            [self.ping, "-I", dev, f"-c{self.count}", f"-W{self.wait}", via],
            capture_output=True)
        return r.returncode == 0


class RealVerify:
    """[FROGNET_ALIVE_9009_V1] FrogNet-Alive measurement over the candidate route.

    The bash/ICMP `ping -I dev <dest1>` form was a REGRESSION: ICMP reachability
    of .1 says nothing about whether the FrogNet daemon path forwards end-to-end,
    and it cannot be the route arbiter (it is exactly the .2-echo mistake one layer
    over). The real liveness signal is the proxy-layer alive echo added alongside
    HELLO: open the daemon socket on dest1:9009 over the candidate route, send a
    HELLO then an OP_RTT_PING, and read back an OP_RTT_PONG. A PONG proves the
    route carries traffic to the destination's daemon AND yields the network rtt in
    one shot (so the same call gates the candidate and measures it for "shortest
    wins"). No PONG -> not alive -> the candidate does not win, and a dest with no
    alive candidate installs NOTHING (honest absence, never a black-hole fallback).

    alive() returns True iff a PONG comes back; rtt_ms() returns the measured
    network rtt in ms, or None. The route to dest1 must already be installed over
    `dev` (promote installs the winner before verifying; the walk holds the /32).
    UNVALIDATED on-box.
    """
    def __init__(self, port=None, connect_timeout=2.0, frame_timeout=3.0):
        self.port = int(port if port is not None
                        else os.environ.get("FROGNET_DAEMON_PORT", "9009"))
        self.ct = float(connect_timeout)
        self.ft = float(frame_timeout)
        self._last_rtt_ms = None

    @staticmethod
    def _send_frame(sock, payload):
        sock.sendall(struct.pack("!I", len(payload)))
        sock.sendall(payload)

    @staticmethod
    def _recv_frame(sock):
        hdr = b""
        while len(hdr) < 4:
            b = sock.recv(4 - len(hdr))
            if not b:
                raise ConnectionError("short frame header")
            hdr += b
        n = struct.unpack("!I", hdr)[0]
        buf = b""
        while len(buf) < n:
            b = sock.recv(n - len(buf))
            if not b:
                raise ConnectionError("short frame body")
            buf += b
        return buf

    def _ping_pong(self, dest1, dev):
        """Returns rtt in ms on a PONG, the string "LOOP" if the daemon
        short-circuits because this connection hairpinned back to us
        ([LOOP_DETECT_9009_V1]), else None. Binds to dev so the probe rides the
        candidate's route even if a broader route also matches."""
        self._last_fail = None            # [NF_FAIL_DETAIL_V1]
        import socket as _s
        from core.semcache_wire import (wrap_hello, wrap_rtt_ping, try_parse,
                                         OP_RTT_PONG, OP_RTT_LOOP)
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        try:
            try:
                sock.setsockopt(_s.SOL_SOCKET, _s.SO_BINDTODEVICE, dev.encode())
            except (OSError, AttributeError) as _bind_e:
                # [FAILFAST_V1] Unpinned probe measures the WRONG PATH (see
                # [PINGPONG_RETURN_DEV_V1]); legitimate when unprivileged (sim),
                # but never silent.
                _LOG.warning(f"[PINGPONG] BINDTODEVICE FAILED dev={dev} err={_bind_e} "
                    f"- probe rides installed route, not the candidate")
            sock.settimeout(self.ct)
            sock.connect((dest1, self.port))
            sock.settimeout(self.ft)
            self._send_frame(sock, wrap_hello(_get_alive_return_ip(dev)))
            t_send = time.monotonic_ns()
            self._send_frame(sock, wrap_rtt_ping(ping_id=1,
                                                 proxy_t_send_ns=t_send,
                                                 pad_len=0))
            reply = self._recv_frame(sock)
            t_back = time.monotonic_ns()
            msg = try_parse(reply)
            if msg is not None and msg.op == OP_RTT_LOOP:
                return "LOOP"        # [LOOP_DETECT_9009_V1] hard verdict
            if msg is None or msg.op != OP_RTT_PONG:
                return None
            return (t_back - t_send) / 1e6
        except _s.timeout as _e:
            # [NF_CONTRACT_V1] Timeout is NOT definitive absence - a real node
            # can be slow. Must never lead to a not_frognet mark.
            self._last_fail = f"timeout({_e})"
            return None
        except (ConnectionRefusedError,) as _e:
            self._last_fail = "ECONNREFUSED"
            return "REFUSED"                # structurally nothing there
        except OSError as _e:
            import errno as _errno
            if _e.errno in (_errno.EHOSTUNREACH, _errno.ENETUNREACH,
                            _errno.ECONNREFUSED):
                self._last_fail = f"errno={_errno.errorcode.get(_e.errno, _e.errno)}"
                return "REFUSED"            # definitive: nothing FrogNet here
            self._last_fail = f"oserror={_e!r}"
            return None                     # anything else: unknown, not definitive
        except ValueError as _e:
            # Something LIVE answered garbage - not absence, but say so loudly.
            _LOG.warning(f"[PINGPONG] PROTOCOL GARBAGE from {dest1}:{self.port} err={_e}")
            return None
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def alive(self, dest1, dev):
        v = self._ping_pong(dest1, dev)
        # A LOOP verdict is NOT alive: a route that hairpins back through us is
        # not a usable path to the dest. [LOOP_DETECT_9009_V1]
        self._last_rtt_ms = v if isinstance(v, float) else None
        return isinstance(v, float)

    def rtt_ms(self, dest1, dev):
        v = self._ping_pong(dest1, dev)
        return v if isinstance(v, float) else None

    def measure_or_loop(self, target, dev):
        """[LOOP_DETECT_9009_V1] Candidate-entry probe over the pinned /32 to
        `target` (the dest .2 the echo already rode; the daemon binds 0.0.0.0 so
        target:9009 reaches it on the SAME candidate route). Returns the string
        "LOOP" if that route hairpins back through us, a float rtt(ms) on a clean
        PONG, or None if not alive. Stateless - re-derived every merge."""
        return self._ping_pong(target, dev)


def _get_alive_return_ip(dev=None):
    """Return the 10.x the far daemon should treat as our return path.

    [PINGPONG_RETURN_DEV_V1] It MUST be the address that egresses `dev`. The
    probe is SO_BINDTODEVICE-pinned to `dev`, so a direct-tunnel probe has to
    advertise THAT tunnel's local /30 - not our eth0 identity. Advertising the
    eth0 identity routes the far end's return leg back through the fabric, a
    hairpin the daemon's [LOOP_DETECT_9009_V1] then (correctly) flags RTT_LOOP -
    a false loop on what is actually a direct call. With the dev's own address
    the return leg rides the same /30, so a direct probe never loops.
    Fall back to the hostname identity only when no dev is given or the dev has
    no 10.x (e.g. an un-pinned probe)."""
    import socket as _s
    if dev:
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr", "show", "dev", dev], text=True)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[2] == "inet":
                    ip = parts[3].split("/")[0]
                    if ip.startswith("10."):
                        return ip
        except (OSError, subprocess.SubprocessError):
            pass
    # [HOSTS_ONLY_V1] No resolver, not even for our own name. Every FrogNet node's
    # hostname is "FrogNetHost", so asking the resolver what that means is asking a
    # question with a different answer on every box -- and on a node whose resolv.conf
    # starts `nameserver 127.0.0.1`, an answer that need not match /etc/hosts. The `ip`
    # command above is the real source; if it did not answer, /etc/hosts is the only
    # other place to look.
    try:
        from core.hosts_only import try_resolve
        import socket as _sk
        for _n in (_sk.gethostname(), "FrogNetHost"):
            _ip = try_resolve(_n)
            if _ip and _ip.startswith("10."):
                return _ip
    except Exception:
        pass
    return "0.0.0.0"


class RealReflect:
    """[REFLECT_PROBE_V1 emitter] Fire a reflect probe at the shipped reflect
    vhost (proxy FrogNetReflectHandler) and classify the verdict.

        GET http://<target>:<REFLECT_PORT>/reflect?o=<o>&c=<counter>
        Host: <target>

      200  REFLECT_OK              -> "OK"    (reached destination; path valid)
      508  REFLECT_LOOP / MAXHOPS  -> "LOOP"  (path bends back through us; reject)
      else / timeout / 502         -> None    (unreachable, or reflect not trapped
                                               on this path - caller treats as
                                               "proceed", never as a loop)

    Port and timeout match the handler's env knobs (FROGNET_REFLECT_PORT 18432,
    FROGNET_REFLECT_TIMEOUT 10).
    """
    def __init__(self, curl="/usr/bin/curl", reflect_port=None, timeout=None):
        self.curl = curl
        self.port = int(reflect_port if reflect_port is not None
                        else os.environ.get("FROGNET_REFLECT_PORT", "18432"))
        self.t = int(timeout if timeout is not None
                     else os.environ.get("FROGNET_REFLECT_TIMEOUT", "10"))

    def probe(self, o, target, counter=0):
        # [REFLECT_EXPUNGED_V1] Plain GET that reads the BODY - never the banned
        # `-o /dev/null -w %{http_code}` status-poke. The reflect handler returns
        # REFLECT_OK / REFLECT_LOOP in the body; -f makes curl exit non-zero on an
        # HTTP error (the else/unreachable case) so we classify from the body we
        # actually read, not a discarded response.
        url = f"http://{target}:{self.port}/reflect?o={o}&c={counter}"
        try:
            r = subprocess.run(
                [self.curl, "-sS", "--connect-timeout", "2",
                 "--max-time", str(self.t), "-H", f"Host: {target}", url],
                capture_output=True, text=True)
        except Exception:
            return None
        body = (r.stdout or "")
        if "REFLECT_OK" in body:
            return "OK"
        if "REFLECT_LOOP" in body or "MAXHOPS" in body:
            return "LOOP"
        return None
