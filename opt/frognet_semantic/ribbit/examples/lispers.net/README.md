# Ribbit-LISP -- install, build and run

Ribbit-LISP is a LISP map-server and map-resolver whose state lives in FrogNet shared memory, tested against
lispers.net 0.643. The experiment -- its question, design and results -- is described in `README-EXPERIMENT.md`;
the full account, component by component, is `RIBBIT-LISP-REPORT.md`; `DOCUMENTS.md` lists every document in
the package. This file is how to run it yourself.

## 1. What you need

Three roles. They can be three machines (as in the reported runs), or fewer; the RAM host must be reachable by the
other two.

| role | runs | reported runs used |
|---|---|---|
| **RAM host** | `lisper-ram` (the LISP region's shared memory, one executable) and, for the performance part, its monitor | a 2-core DigitalOcean droplet on the Internet, Ubuntu (Linux 5.15, g++ 11) |
| **systems machine** | lispers.net 0.643 and Ribbit-LISP's front, side by side, driven by an agent | an Intel i7 box, Ubuntu |
| **test machine** | the harness: sends the LISP traffic, collects the results | a Raspberry Pi 5, Raspberry Pi OS (aarch64) |

**Every machine** (Ubuntu package names): `g++` (11 or later), `liblz4-dev`, `libssl-dev`, `python3`.

**The systems machine**, in addition:

- **Python 3.12.** The lispers.net 0.643 release is compiled bytecode only, built for Python 3.12; no other version
  can run it. Ubuntu 24.04 has 3.12.
- **lispers.net 0.643, the Python 3 release:** `lispers.net-release-py3-0.643.tgz`, in lispers.net's repository
  under `build/releases/release-py3-0.643/`. Unpack it in a directory of your choice.
- **`tcsh`** and a `python` command (`python-is-python3`): lispers.net's `RUN-LISP` and `STOP-LISP` are tcsh
  scripts that call `python`.
- **The Python modules lispers.net imports**, installed for root (the agent runs lispers.net as root): `cheroot`,
  `bottle`, `netifaces`, `pcapy`, `OpenSSL`, `requests`, `geopy`, `ecdsa`, `Crypto`, `pytun`, `future`, `distro`,
  `curve25519`. These are import names; lispers.net's own `how-to-install-ubuntu.txt` gives its pip command, but it
  was written for Python 2, and some packages have different names or forks for Python 3.12. The agent checks
  every one before it starts and names any that are missing.
- **root:** the agent starts and stops lispers.net, whose `STOP-LISP` uses `sudo kill`.

**For the matched audit** (optional): `pip install lizard`, the lispers.net source (`git clone
https://github.com/farinacci/lispers.net`), and the installed release it is checked against.

## 2. Build (every machine)

```
cd opt/frognet_semantic/ribbit/examples/lispers.net       # in the Ribbit tree; the platform is ../../cpp
STAGES=build tools/qualify.sh
```

It must end `build PASS` under the banner `RIBBIT-LISP v0.64-vendor-ram`. Compiler output is shown as it happens. The
build compiles as many programs at once as the machine has cores; on a small machine, `JOBS=1 STAGES=build
tools/qualify.sh` builds one at a time, and nothing large should be running alongside it.

To check the build on one machine before involving the others:

```
tools/qualify.sh
```

runs every qualification stage against a local RAM host (`RIBBIT-LISP-REPORT.md` section 6.1). Two stages compare
against reference implementations and are skipped unless you point at them: `LISPERS_ROOT=<dir containing
lisp/lisp.py>` for the byte oracle, `FROGNET_SEMANTIC_ROOT=<FrogNet semantic engine>` for the wire oracles.

## 3. The RAM host -- on the Internet, at an address and port you choose

Pick any machine the other two can reach, and any TCP port. Below, `<RAM_HOST>` is its name or address and
`<RAM_PORT>` the port.

**Open the port** to the systems machine and the test machine, and to nothing else:

```
sudo ufw allow from <SYSTEMS_MACHINE_IP> to any port <RAM_PORT> proto tcp
sudo ufw allow from <TEST_MACHINE_IP> to any port <RAM_PORT> proto tcp
```

The RAM host has no authentication of its own: anyone who can reach the port can read and write the shared
memory (except the region's private rows, which hold the site keys and are never served). Restrict it by address,
as above, or put it on a private network.

**Start it** in the background, so a dropped SSH session does not stop it:

```
cd <package directory>
setsid nohup ./ribbit_cpp/lisper-ram --listen 0.0.0.0:<RAM_PORT> > ~/ram-server.log 2>&1 < /dev/null &
cat ~/ram-server.log
```

The log's first line names the version and the address it listens on. A fresh start is a fresh, empty memory;
restart it before a run you intend to publish.

**For the performance part**, also start its monitor on the same machine:

```
setsid nohup python3 tools/acceptance.py --monitor ram --ram 127.0.0.1:<RAM_PORT> > ~/ram-monitor.log 2>&1 < /dev/null &
```

`setsid` returns at once and the shell reports the job "Done": that is the detaching, not the program ending.
`pgrep -af "lisper-ram|acceptance.py --monitor"` shows both running.

## 4. The systems machine -- the agent

```
cd <package directory>
sudo setsid nohup python3 tools/acceptance.py --agent --ram <RAM_HOST>:<RAM_PORT> \
     --lispers-dir <where lispers.net 0.643 is unpacked> > ~/agent.log 2>&1 < /dev/null &
head ~/agent.log
```

The agent checks that `tcsh`, `python3` and lispers.net's modules are present, prints the address it will use,
and waits for the harness. It starts lispers.net on UDP 4342 (and its own API on 8080; `--lispers-api-port` to
change) and Ribbit's front on UDP 14342 (`--ribbit-udp` to change); the test machine must be able to reach both
UDP ports. Leave it running.

## 5. The test machine -- the harness

```
cd <package directory>
setsid nohup python3 tools/acceptance.py --ram <RAM_HOST>:<RAM_PORT> --out acceptance-out > ~/run.log 2>&1 < /dev/null &
tail -f ~/run.log
```

`tail -f` only watches; stopping it, or losing the session, does not stop the run. The full run takes about an
hour: the 78 behaviour tests, then the performance schedule. The harness prints the address it uses and the
monitors it can hear at the start of the performance part (`monitors publishing: control, ram`).

Useful options:

| option | effect |
|---|---|
| `--skip-perf` | behaviour tests only (about 12 minutes) |
| `--perf-only` | performance only |
| `--perf-quick` | performance, 3-second runs: to check every machine reports before a long run |
| `--only L3.9,L2.16` | only these tests |
| `--clients 1,4,16,64`, `--scale 1,100,1000,10000`, `--duration 20`, `--warmup 5` | the performance schedule |
| `--load-procs N` | load-generator processes (default: this machine's cores) |
| `--local-ip IP` | this machine's address, if it has several and the wrong one is chosen |

**Results**, on the test machine in `acceptance-out/`:

- `ACCEPTANCE-REPORT.md` -- every test, both systems' answers, the classification, the evidence
- `PERF-REPORT.md`, `perf.json`, `PERF-TIMELINE.html` -- throughput, latency, and every machine's counters

Start the three roles in this order: RAM host (and monitor), agent, harness.

## 6. The matched audit

On any machine with the package, `lizard`, the lispers.net source and the installed release:

```
pip install lizard
python3 tools/matched_audit.py --lispers-src <lispers.net repository>/lisp \
        --lispers-release <installed lispers.net 0.643> --out matched-audit
```

The first line printed is the check that the source is the release (`26 of 26 files compile to identical
bytecode`); if it is not, nothing is measured. The rules, the result and every function in or out of scope, with
the reason, are written to `matched-audit/`.

## 7. The LISP client library

`build/liblisper.so` (C interface `client/lisper.h`) is `lisper::Client`: any LISP region operation by name, a whole
Map-Register in and the Map-Notify out, a lookup, sites -- each one call to `lisper-ram`. `client/lisper.py` loads it
from Python through ctypes. The `client` qualification stage runs its tests from C++ and from Python against a real
`lisper-ram`, the Python one sending authenticated Map-Registers built by the acceptance suite's own encoder.

## 8. Running Ribbit-LISP as a map-server (outside the tests)

`lisp-service` is the deployable program. On each LISP machine, with a RAM host running as in section 3:

```
sudo mkdir -p /etc/lispers.d
sudo cp my-lisp-service.config /etc/lispers.d/lisp-service.config
./tools/lisp-service --service          # detached; log in tools/logs/lisp-service.log
```

A minimal map-server configuration (JSON):

```
{
  "name": "ms1",
  "ram": {"host": "<RAM_HOST>", "port": <RAM_PORT>, "api": "/lisper-api"},
  "roles": ["map-server", "map-resolver"],
  "udp": {"address": "<this machine's address>", "port": 4342},
  "heartbeat_s": 10,
  "startup_wait_s": 5,
  "sites": [{"iid": "0", "prefix": "198.18.0.0/16", "group": "", "accept_more_specifics": true,
             "key_id": 1, "password": "<the site's key>"}]
}
```

`udp.address` is the address other LISP machines reach this one at (it is advertised in the memory, so not
`0.0.0.0`). Every key is described at the top of `tools/lisp_service.cpp`. `tools/make_hw_configs.py` writes matching
lispers.net and Ribbit configurations from the test fixture, for the two-host DDT tests in `REAL-HARDWARE-TESTS.md`.

## 9. Where this has run -- and a request

Every run reported here was made on the author's own machines, and every one of them has had FrogNet installed
for a long time. Nothing in this package should depend on that, but nothing has proven it doesn't: this package
has not yet been run on machines that have never seen FrogNet.

If you can, please try all three roles on clean configurations, and tell me what fails -- the command, what it
printed, and the machine's OS and Python version. The results directory (`acceptance-out/`), `~/agent.log` from
the systems machine and `~/ram-server.log` from the RAM host are the most useful things to send back. Every
failure on a clean machine is a fix to this package, and a finding in its own right.

## 10. When something goes wrong

- **A build that sits for minutes without output** is usually out of memory: check `free -m`, stop anything large
  (an old RAM host holds everything it was given since it started), and use `JOBS=1`.
- **`no 'ram' monitor is publishing`**: the RAM host's monitor is not running; its numbers will be missing from the
  performance report.
- **`lispers.net stopped serving: restarting`** during the behaviour tests is expected: several tests stop
  lispers.net 0.643, and the harness records that as evidence and restarts it.
- **A run that stops when an SSH session drops**: start every role with `setsid nohup` as shown.
- **The harness picks the wrong address** on a machine with several: `--local-ip`.
