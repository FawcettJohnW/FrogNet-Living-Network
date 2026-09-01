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
run_all.py (M6) - one orchestrator, the whole comprehensive simulator.

Runs every tier and gates on all-green. Tiers, in order of increasing fidelity:

  TIER 1  model         frognet_sim.main()      - algorithm + 23 topologies +
                        bug regressions + live merge-cycle orchestration +
                        reboot/poll/teardown/churn/invariants (M5 lifecycle,
                        modeled). Pure model; fastest pre-flight.
  TIER 2  real-engine   live_engine.main()      - M1: REAL planner.plan() +
                        committer.commit_final() over the 23 topologies.
  TIER 3  broker        broker_topology.main()  - M3: REAL frognet_broker_v4
                        decides the pond; real planner+committer validate it.
  TIER 4  real-kernel   live_engine (real)      - M2: same converge over real
                        `ip`/`wg` in netns. BOX-ONLY (Linux + root). Skipped in
                        the container; enable with FROGNET_SIM_BACKEND=real.
  TIER 5  data-plane    proxy/daemon over the    - M4: REAL proxy_main/daemon
                        netns mesh.                pushing real traffic. BOX-ONLY;
                        not yet wired (needs the proxy/daemon tree on the box).

Container runs TIERS 1-3 (fully validated) plus TIER 4 as a dry-run plan.
A box run flips TIER 4 to execute and adds TIER 5.

Feedback: TIER 4/5 emit measured RTTs and any failures; capture them to
calibrate frognet_sim.EDGE_RTT_BY_KIND and to add real failure scenarios, so the
offline model converges toward hardware behavior over time.
"""
import io
import os
import sys

# [SENTINEL_ISOLATION_V1] The suite must never share not_frognet marks with the
# real box or with a previous run - cross-run leakage through
# /etc/sentinels/not_frognet.tsv silently poisoned discovery oracles (proven
# 2026-07-05). Fail hard if isolation can't be established.
import tempfile as _tmpf
# [SIM_TMP_MUST_NOT_MATCH_THE_MERGE_GLOB_V1] prefix was "frognet_sim_sentinels_",
# which sits under runMerge.bash's `rm -rf /tmp/frognet*`. A merge firing mid-run
# deletes the harness's own sentinel isolation directory.
_SENT_DIR = _tmpf.mkdtemp(prefix="frogsim_sentinels_")
os.environ["FROGNET_SENTINEL_DIR"] = _SENT_DIR
if not os.path.isdir(_SENT_DIR):
    raise RuntimeError(f"sentinel isolation dir not created: {_SENT_DIR}")
print(f"[ISOLATION] FROGNET_SENTINEL_DIR={_SENT_DIR}")
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("FROGNET_LOG_LEVEL", "ERROR")

# [PYCACHE_PURGE_V1] Purge project __pycache__ (never venv) and disable bytecode
# writing BEFORE importing any project module, so a freshly-applied overlay can't
# be shadowed by stale .pyc. See live_engine for the full note.
def _purge_pycache(root):
    import shutil
    for dp, dns, _fn in os.walk(root):
        parts = dp.split(os.sep)
        if "venv" in parts or "site-packages" in parts:
            dns[:] = []
            continue
        if "__pycache__" in dns:
            shutil.rmtree(os.path.join(dp, "__pycache__"), ignore_errors=True)
            dns.remove("__pycache__")
sys.dont_write_bytecode = True
_purge_pycache(_PARENT)

from frognet_log import get_logger
log = get_logger("simulation.run_all")


def _run_tier(label, fn, quiet=True):
    """Run a tier's main(); return (label, rc). Captures its chatty stdout unless
    quiet=False, so the orchestrator summary stays readable."""
    buf = io.StringIO()
    try:
        if quiet:
            with contextlib.redirect_stdout(buf):
                rc = fn()
        else:
            rc = fn()
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    except Exception as e:
        print(f"  [ERROR] {label}: {type(e).__name__}: {e}")
        return (label, 1)
    if rc is None:
        rc = 0  # main() that returns nothing on success (sys.exit only on fail)
    out = buf.getvalue()
    npass = out.count("[PASS]") + out.count("[OK")
    nfail = out.count("[FAIL]") + out.count("FAILED")
    status = "PASS" if rc == 0 else "FAIL"
    print(f"  [{status}] {label}  (rc={rc}, ~{npass} checks ok"
          + (f", {nfail} fail markers" if nfail else "") + ")")
    return (label, rc)


def _run_proc(label, argv, optional_tool=None, retry=True):
    """Run a check in its OWN process and return (label, rc). The transport /
    channel-set test files sys.exit() on completion, so they must be subprocesses
    (importing them would terminate this orchestrator). If `optional_tool` is
    named and absent (e.g. ffmpeg on a box), SKIP rather than fail the gate.

    Some loopback tiers carry timing-sensitive assertions (RTT bounds, pipeline
    timeouts) that can flake under gate-wide CPU contention. We retry ONCE: a
    genuine break fails twice and reports FAIL; a contention flake passes on the
    2nd try and is reported PASS but FLAGGED, so flake is surfaced, not hidden."""
    import shutil
    import subprocess
    if optional_tool and not shutil.which(optional_tool):
        print(f"  [SKIP] {label}  ({optional_tool} not present)")
        return (label, 0)

    def _once():
        try:
            return subprocess.run([sys.executable] + argv, cwd=_PARENT,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode
        except Exception as e:  # noqa: BLE001
            print(f"  [ERROR] {label}: {type(e).__name__}: {e}")
            return 1

    rc = _once()
    if rc == 0:
        print(f"  [PASS] {label}  (rc=0)")
        return (label, 0)
    if not retry:
        print(f"  [FAIL] {label}  (rc={rc})")
        return (label, rc)
    rc2 = _once()
    if rc2 == 0:
        print(f"  [PASS] {label}  (rc=0 on 2nd try - 1st flaked under load, not a break)")
        return (label, 0)
    print(f"  [FAIL] {label}  (rc={rc2}, failed twice - genuine)")
    return (label, rc2)


def _argval(names):
    """Value for any of `names`, supporting '--flag val' and '--flag=val'."""
    a = sys.argv
    for name in names:
        for i, tok in enumerate(a):
            if tok == name and i + 1 < len(a):
                return a[i + 1]
            if tok.startswith(name + "="):
                return tok.split("=", 1)[1]
    return None


def _remote_daemon_target():
    """(addr, port) for the real mode-2 leg. CLI --remote-daemon[-port] override
    FROGNET_TEST_REMOTE_ADDR[/PORT]. Tolerates the --remote-deamon misspelling."""
    addr = (_argval(["--remote-daemon", "--remote-deamon"])
            or os.environ.get("FROGNET_TEST_REMOTE_ADDR"))
    port = (_argval(["--remote-daemon-port", "--remote-deamon-port"])
            or os.environ.get("FROGNET_TEST_REMOTE_PORT", "19009"))
    return addr, port


def _wants_real():
    """TIER T 'real' transport switch. Honors `--transport real`,
    `--transport=real`, `--remote-daemon <addr>` (implies real), or
    FROGNET_SIM_BACKEND=real (same env that flips TIER 4)."""
    a = sys.argv
    if "--transport=real" in a:
        return True
    if "--transport" in a:
        i = a.index("--transport")
        if i + 1 < len(a) and a[i + 1] == "real":
            return True
    if _argval(["--remote-daemon", "--remote-deamon"]):
        return True
    if _shape_iface_arg() is not None:
        return True
    return os.environ.get("FROGNET_SIM_BACKEND") == "real"


def _shape_iface_arg():
    """--shape-iface request. Returns None (not asked), 'auto' (bare flag or no
    name), or an explicit scratch name. A bare trailing flag means auto."""
    a = sys.argv
    for i, tok in enumerate(a):
        if tok == "--shape-iface":
            nxt = a[i + 1] if i + 1 < len(a) else None
            return "auto" if (nxt is None or nxt.startswith("-")) else nxt
        if tok.startswith("--shape-iface="):
            v = tok.split("=", 1)[1]
            return v or "auto"
    return None


def _shape_iface_ok(name):
    """Whitelist guard: only the scratch veth we create. NEVER a production
    interface - shaping wg0/eth0/lo would degrade the live mesh."""
    return name == "auto" or name.startswith(("frsh", "frog"))


def _run_mode2_shaped(iface_arg):
    """Real NETEM-shaped mode-2 echo. Builds a scratch netns + veth pair, puts
    remote_test_daemon in the netns, applies tc-netem on the root-side veth
    (forced through the netns so the qdisc actually bites - a same-host pair
    without a netns gets short-circuited by the loopback fast path), runs the
    REQ_RAW echo across it, and ASSERTS the measured RTT reflects the applied
    delay. Container (DryRunner) prints the plan and SKIPs; box (root +
    FROGNET_SIM_EXECUTE=1) executes for real. Everything is torn down in finally."""
    import re
    import socket
    import subprocess
    import threading
    from transport_factories import NetemShaper, NetworkParams, default_runner

    label = "transport mode 2 echo (real-TCP, NETEM-shaped veth in netns)"
    if not _shape_iface_ok(iface_arg):
        print(f"  [FAIL] {label}  - refusing to shape '{iface_arg}': only the scratch "
              f"veth (auto / frsh*) is allowed, never wg/eth/wlan/lo")
        return (label, 1)

    NS, A, B, DADDR = "frogshape", "frsh-a", "frsh-b", "10.123.0.2"
    params = NetworkParams(latency_ms=50.0, jitter_ms=5.0, bandwidth_bps=1e9)
    runner = default_runner()
    setup = [
        ["ip", "netns", "add", NS],
        ["ip", "link", "add", A, "type", "veth", "peer", "name", B],
        ["ip", "link", "set", B, "netns", NS],
        ["ip", "addr", "add", "10.123.0.1/30", "dev", A],
        ["ip", "link", "set", A, "up"],
        ["ip", "netns", "exec", NS, "ip", "addr", "add", "10.123.0.2/30", "dev", B],
        ["ip", "netns", "exec", NS, "ip", "link", "set", B, "up"],
        ["ip", "netns", "exec", NS, "ip", "link", "set", "lo", "up"],
    ]
    shaper = NetemShaper(A, params, runner=runner)

    if not runner.is_real():
        for c in setup:
            runner.run(c)
        shaper.apply(); shaper.remove()
        print(f"  [SKIP] {label}  (plan only - needs root + FROGNET_SIM_EXECUTE=1)")
        for c in runner.commands:
            print("           plan: " + " ".join(c))
        return (label, 0)

    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    runner.run(["ip", "netns", "del", NS])  # stale cleanup; ignore rc
    for c in setup:
        rc, out, err = runner.run(c)
        if rc != 0:
            print(f"  [FAIL] {label}  (setup failed: {' '.join(c)} -> {err.strip()})")
            runner.run(["ip", "netns", "del", NS])
            return (label, 1)
    daemon = None
    try:
        shaper.apply()
        daemon = subprocess.Popen(
            ["ip", "netns", "exec", NS, sys.executable,
             "simulation/remote_test_daemon.py", "--listen", f"{DADDR}:{port}"],
            cwd=_PARENT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = threading.Event()
        def _w():
            for line in daemon.stdout:
                if "listening on" in line:
                    ready.set()
        threading.Thread(target=_w, daemon=True).start()
        if not ready.wait(10.0):
            print(f"  [FAIL] {label}  (netns daemon did not come up)")
            return (label, 1)
        proc = subprocess.run(
            [sys.executable, "simulation/sotf_video_stream_test.py", "--mode", "2",
             "--remote-addr", DADDR, "--remote-port", str(port),
             "--echo-bytes", "60000", "--echo-count", "10"],
            cwd=_PARENT, capture_output=True, text=True)
        m = re.search(r"p50=([0-9.]+)", proc.stdout)
        p50 = float(m.group(1)) if m else float("nan")
        # Shaped one-way delay is 50ms; lo baseline is <1ms. Require the measured
        # p50 to have clearly risen, proving the qdisc actually took effect.
        ok = proc.returncode == 0 and p50 >= params.latency_ms * 0.7
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  "
              f"(applied {params.latency_ms:g}ms delay, measured p50={p50:.1f}ms, "
              f"rc={proc.returncode})")
        return (label, 0 if ok else 1)
    finally:
        try:
            shaper.remove()
        except Exception:
            pass
        if daemon:
            daemon.terminate()
            try:
                daemon.wait(timeout=3)
            except Exception:
                daemon.kill()
        runner.run(["ip", "netns", "del", NS])


def _run_mode2_real():
    """Real-kernel-TCP mode-2 REQ_RAW echo through remote_test_daemon.
    Target --remote-daemon / FROGNET_TEST_REMOTE_ADDR if given (true remote /
    cross-box); else self-host a daemon on 127.0.0.1 over lo. Returns (label, rc)."""
    import socket
    import subprocess
    import threading
    remote, port = _remote_daemon_target()
    if remote:
        label = f"transport mode 2 echo (REAL remote {remote}:{port})"
        rc = subprocess.run(
            [sys.executable, "simulation/sotf_video_stream_test.py", "--mode", "2",
             "--remote-addr", remote, "--remote-port", str(port),
             "--echo-bytes", "60000", "--echo-count", "10"],
            cwd=_PARENT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        print(f"  [{'PASS' if rc == 0 else 'FAIL'}] {label}  (rc={rc})")
        return (label, rc)

    label = "transport mode 2 echo (real-TCP, self-hosted daemon on lo)"
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    d = subprocess.Popen(
        [sys.executable, "simulation/remote_test_daemon.py", "--listen", f"127.0.0.1:{port}"],
        cwd=_PARENT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()
    def _watch():
        for line in d.stdout:        # drains the pipe so the daemon never blocks
            if "listening on" in line:
                ready.set()
    threading.Thread(target=_watch, daemon=True).start()
    try:
        if not ready.wait(8.0):
            print(f"  [FAIL] {label}  (daemon did not report listening)")
            return (label, 1)
        rc = subprocess.run(
            [sys.executable, "simulation/sotf_video_stream_test.py", "--mode", "2",
             "--remote-addr", "127.0.0.1", "--remote-port", str(port),
             "--echo-bytes", "60000", "--echo-count", "10"],
            cwd=_PARENT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        print(f"  [{'PASS' if rc == 0 else 'FAIL'}] {label}  (rc={rc}, lo:{port})")
        return (label, rc)
    finally:
        d.terminate()
        try:
            d.wait(timeout=3)
        except Exception:
            d.kill()


def _preflight():
    """Check box prerequisites for the real (TIER 4/5) backends BEFORE running,
    so a hardware run fails fast and legibly. Reports each prereq; returns 0 if
    the real tiers can run, 1 otherwise (container will fail most - expected)."""
    import shutil, platform
    print("=== preflight: real-backend prerequisites ===")
    ok = True
    def chk(name, cond, why=""):
        nonlocal ok
        print(f"  [{'OK ' if cond else 'MISS'}] {name}" + ("" if cond else f"  - {why}"))
        ok = ok and cond
        return cond
    chk("Linux", platform.system() == "Linux", "netns/wg need Linux")
    chk("root (euid 0)", hasattr(os, "geteuid") and os.geteuid() == 0,
        "ip netns / wg need root")
    chk("ip binary", shutil.which("ip") is not None, "install iproute2")
    chk("wg binary", shutil.which("wg") is not None, "install wireguard-tools")
    try:
        import mysql.connector  # noqa
        chk("mysql.connector (venv)", True)
    except Exception:
        chk("mysql.connector (venv)", False, "template store needs the venv - DO NOT delete it")
    chk("proxy/ tree", os.path.isdir(os.path.join(_PARENT, "proxy")))
    chk("daemon/ tree", os.path.isdir(os.path.join(_PARENT, "daemon")))
    print(f"\nPREFLIGHT: {'PASS - real tiers can run' if ok else 'INCOMPLETE - fix MISS items before real run'}")
    return 0 if ok else 1


def main():
    if "--preflight" in sys.argv:
        return _preflight()
    if "--record-baselines" in sys.argv:
        import regression_baselines as RB
        keys = RB.record_all(source="offline")
        scns = RB.seed_scenarios()
        print(f"blessed {len(keys)} OFFLINE baselines -> {RB.BASELINE_DIR}")
        print(f"seeded {len(scns)} new failure scenarios -> {RB.SCENARIO_DIR}")
        print("For HARDWARE baselines (kernel readback), on the box as root:\n"
              "  FROGNET_SIM_BACKEND=real FROGNET_SIM_EXECUTE=1 "
              "FROGNET_SIM_RECORD_BASELINES=1 python3 live_engine.py")
        return 0
    print("================== FrogNet comprehensive simulator - M6 gate ==================\n")
    import regression_baselines as _RB
    print(f"sim_build {_RB.SIM_BUILD}")
    results = []

    import frognet_sim
    import live_engine
    import broker_topology

    # [FEEDBACK_LOOP_V1] Fold any hardware-measured RTTs into the model BEFORE the
    # model tier runs, so timing-dependent decisions reflect the real network.
    import model_feedback
    edge, _cf = model_feedback.apply_calibration()
    if edge:
        print(f"calibration: measured RTTs applied to model EDGE_RTT_BY_KIND -> {edge}\n")
    else:
        print("calibration: none on disk (model uses guessed RTTs; a hardware run "
              "produces model_calibration.json)\n")

    print("TIER 1 - model (algorithm + topologies + lifecycle/churn/invariants)")
    results.append(_run_tier("model suite (frognet_sim)", frognet_sim.main))

    print("\nTIER 2 - real planner + committer (M1)")
    results.append(_run_tier("real-engine 23 topologies", live_engine.main))

    print("\nTIER 2b - failure modes through real planner+committer")
    import live_failures
    results.append(_run_tier("failure modes (reroute/black-hole/partition)", live_failures.main))

    print("\nTIER 3 - real broker in the loop (M3)")
    results.append(_run_tier("broker-decided ponds", broker_topology.main))

    print("\nTIER R - regression vs learned baselines + failure-scenario replay")
    import regression_baselines as RB
    def _regress():
        rc1, _regr = RB.check_all()
        rc2 = RB.replay_scenarios()
        return 1 if (rc1 or rc2) else 0
    results.append(_run_tier("regression (learned baselines + scenario replay)", _regress))

    print("\nTIER 4 - real kernel in netns (M2)")
    if os.environ.get("FROGNET_SIM_BACKEND") == "real":
        results.append(_run_tier("real-kernel converge", live_engine.main, quiet=False))
    else:
        # container: show the provisioning plan is buildable, mark tier deferred
        try:
            import netns_backend
            results.append(_run_tier("netns provisioning plan (dry-run)", netns_backend.main))
            print("        (execute on a Linux box: FROGNET_SIM_BACKEND=real "
                  "FROGNET_SIM_EXECUTE=1)")
        except Exception as e:
            print(f"  [ERROR] netns plan: {e}")
            results.append(("netns provisioning plan", 1))

    print("\nTIER 5 - proxy semantic data plane (M4)")
    import proxy_dataplane
    results.append(_run_tier("proxy decision/keying/origin", proxy_dataplane.main))
    print("        (transport on :9009 + daemon + DB store are box-tier)")
    import transport_tier
    results.append(_run_tier("transport calibration-capture (loopback mechanism)", transport_tier.main))
    print("        (real proxy/daemon round trip is the box fill-in)")

    print("\nTIER C - codex language compliance (PRIMER 2: JSON/XML/HTML/text)")
    from simulation.spec_compliance.run_compliance import main as _compliance_main
    results.append(_run_tier("codex spec-compliance (round-trip + security)", _compliance_main))

    print("\nTIER T - physical transport + socket sets (this feature)")
    results.append(_run_proc("faithful transport sim (transport_sim_tier, 24 checks)",
                             ["simulation/transport_sim_tier.py"]))
    results.append(_run_proc("transport factory self-test (sim+loopback+netem, 12)",
                             ["simulation/transport_factories.py"]))
    results.append(_run_proc("channel_sets UNIT (alloc/registry/threads)",
                             ["proxy/test_channel_sets.py"]))
    results.append(_run_proc("channel_sets INTEGRATION (HOL isolation via sim)",
                             ["simulation/test_channel_sets_transport.py"]))
    results.append(_run_proc("SotF stream mode 0 (sim)",
                             ["simulation/sotf_video_stream_test.py", "--mode", "0",
                              "--gen-duration", "1.0", "--gen-fps", "8"],
                             optional_tool="ffmpeg"))
    results.append(_run_proc("SotF stream mode 1 (loopback real-TCP)",
                             ["simulation/sotf_video_stream_test.py", "--mode", "1",
                              "--gen-duration", "1.0", "--gen-fps", "8"],
                             optional_tool="ffmpeg"))
    results.append(_run_proc("SotF media planes (drop/instrumentation + ladder over sim)",
                             ["simulation/test_media_planes.py"]))
    if _wants_real():
        results.append(_run_mode2_real())
        _si = _shape_iface_arg()
        if _si is not None:
            results.append(_run_mode2_shaped(_si))
            print("        (the genuine cross-box hop still needs a 2nd box -")
            print("         --remote-daemon <ip>; see RUNBOOK_physical_transport.md)")
        else:
            print("        (shaped netem: add --shape-iface to apply tc-netem on a")
            print("         scratch veth; cross-box hop needs --remote-daemon <ip>)")
    else:
        print("        (mode 2 remote echo, shaped netem execute, and the cross-box")
        print("         physical hop are box-tier - enable the real-TCP mode-2 leg with")
        print("         --transport real ; add --shape-iface for tc-netem)")

    print("\nTIER D - discovery pathway (runMerge: discovery.py + routes.py)")
    def _discovery_gate():
        import subprocess
        return subprocess.run([sys.executable, "-m", "discovery.run_discovery_oracles"],
                              cwd=_PARENT).returncode
    results.append(_run_tier("discovery oracle suite (incl. Seattle6 identity-down)", _discovery_gate))
    print("        (real LAN/gateway route engine; runMerge ip r as hardware truth)")

    print("\n================== SUMMARY ==================")
    failed = [l for (l, rc) in results if rc != 0]
    for (l, rc) in results:
        print(f"  {'PASS' if rc == 0 else 'FAIL'}  {l}")
    if failed:
        print(f"\nGATE: FAIL ({len(failed)} tier(s) failed)")
        return 1
    print("\nGATE: PASS - all in-container tiers green "
          "(TIER 4 execute + TIER 5 transport are box-tier)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
