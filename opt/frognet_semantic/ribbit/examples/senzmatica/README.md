# Reconstructed SenzMatica remote-configuration checkpoint

The earlier download archive and its commits were lost after workspace rollback.
This is a reconstruction from recorded source changes, with fresh builds/tests.
It is not byte-identical to that missing tarball. No old reported PASS is treated
as a newly executed result.

PLAN.md is the original user-supplied contract recovered from its persistent
file. Its historical execution-state section is unchanged; current results are
in this README and evidence/.

## Workflow

One live switch selects remote configuration through RAM or the original MQTT
command path. Java uses JNI into the existing API++ library; there is no new Java
wire client. The device writes its capability and applied truth. The operator
writes desired state. Matching applied generation/value produces an MQTT
completion which updates the existing Mongo configuration/history and frontend
read API. Other workloads retain MQTT; the gate stores a real sensor sample via
the unchanged telemetry handler/repository.

The software device serves both MQTT and RAM and models one integer setting.
It is test equipment, not SenzMate firmware. Completed RAM generations are stored
in a device-owned file after application so restart after returning to MQTT does
not replay an older RAM setting. Process restart is tested; power-loss durability
and concurrent conflicting changes across the two paths are not established.

## Controls and variants

Original backend is pinned at 1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4 and remains
unchanged. The conversion is in a separate checkout. The optional mixed demo has
a separate two-line-order repair: persist the pending Message/history before
publishing its MQTT command. This avoids a fast device acknowledgment arriving
before its pending record exists. The untouched control sends its test device
response after the original HTTP request returns. The repaired demo uses the
immediate-response dual-path device. These controls must not be described as
identical unmodified timing baselines.

The original source finding/earlier observed failure is described in the chat;
its old raw logs were lost. Fresh evidence is only what is under evidence/.
Complete backend diffs and exact source hashes are included in the archive.

## Fresh verification

- Original application with Mosquitto: 7 checks PASS.
- RAM workflow and frontend read API: 13 checks PASS.
- Repaired live MQTT60 -> RAM90 -> MQTT120, including device restart: 12 PASS.
- JNI bridge against a real native TCP RAM host: 23 checks PASS (18 + 5 for the new HeldRead).
- Semantic mutation controls: 3/3 caught by named behavior assertions; both restored positive runs pass 13 checks.
- Surviving API++ tuple/capability/connection gate: 75 fresh assertions PASS.

See evidence/ for the actual current logs/results. The JNI checker prints a
warning before the first native-method linkage; this is reproduced and remains
unexplained, not a warning-free checker result. The intentionally failing callback
is separately caught/logged and reported through CheckCallbacks.

## Build and run

Requires Python 3.9+, Java 8, GCC/C++20, LZ4/OpenSSL headers, cJSON headers/runtime,
MongoDB 5.0.31 and the stock Mosquitto 2.1.2 source already in the FrogNet tree.
The original dependency bootstrap used Azul Zulu 8.0.512, Maven 3.9.9, MongoDB's
Ubuntu 20.04 package, locally extracted Debian libssl1.1, and cJSON 1.7.17 headers.
No dependencies or compiled bytecode/binaries are shipped in this source overlay.

From FrogNet root, build the unchanged stock broker:

```
make -C opt/frognet_semantic/ribbit/examples/mqtt/stock -j2 WITH_DOCS=no WITH_WEBSOCKETS=no WITH_SQLITE=no WITH_EDITLINE=no WITH_HTTP_API=no DIRS='libcommon lib src client'
```

Set JAVA_HOME and CPATH/LIBRARY_PATH for the listed development headers/libraries.
Then run tools/build_native.sh. The maven.py helper regenerates proxy settings
from the current environment and imports its proxy CA into an isolated truststore.
It installs the exact upstream bundled util JAR with --install-util; no stub is
substituted. Build original before converted checkouts to populate dependencies.

Workspace layout expected by the runners:

- FrogNet-Living-Network: this source tree.
- senzmatica-core: unchanged upstream pin.
- senzmatica-converted: apply tools/apply_backend.py to a fresh upstream worktree.
- senzmatica-converted-repaired: apply with --repair to another fresh worktree.
- baseline-tools: JDK8/Maven/MongoDB paths as recorded in tools/.
- recovery-state: isolated dependency cache and per-run logs.

```
python tools/maven.py --workspace <root> --source <upstream> --state <new-build-state> --install-util
python tools/run.py --workspace <root> --state <new-run-state> --mode original
python tools/check_jni.py --state <new-run-state>
python tools/run.py --workspace <root> --state <new-run-state> --mode ram
python tools/run.py --workspace <root> --state <new-run-state> --mode mixed
python tools/check_mutations.py --workspace <root> --state <new-run-state>
```

All processes run together inside a runner invocation, because separate tool
invocations in this environment isolate loopback networks. Use fresh state paths.
The runners use only local synthetic service credentials/endpoints. They always
save result.json and clean up child processes. Application timing is not inferred
from the oracle's bootstrap/database-observation loops.

## Remaining scope

Real-kernel topology tier (TOPOLOGY.md, tools/topology.py, evidence/topology/): three network
namespaces, silent splits, heal, RAM restart. The first version (evidence/topology-v1/) showed that
sessions never came back after a split longer than the 5 s keepalive. Now each participant sees its own
session end through a held read it owns and takes a fresh one: the device ends and its supervisor
restarts it (node/systemd/senz-device.service, as fdf-client); the Java backend reattaches
in-process and reasserts its own desired truths. Three runs PASS with no manual restarts: 15 s splits
back in ~3 s after heal, RAM restart back in ~2 s, 60 s split ~15 s (kernel SYN backoff in
frogram::dial, a core finding not changed here). Still remaining: FrogNet region/RAM-role
integration, browser validation, wire/code accounting, event-based timing and two-machine trials.

The JNI checker prints a
warning before the first native-method linkage; this is reproduced and remains
unexplained, not a warning-free checker result. The intentionally failing callback
is separately caught/logged and reported through CheckCallbacks.

## Build and run

Requires Python 3.9+, Java 8, GCC/C++20, LZ4/OpenSSL headers, cJSON headers/runtime,
MongoDB 5.0.31 and the stock Mosquitto 2.1.2 source already in the FrogNet tree.
The original dependency bootstrap used Azul Zulu 8.0.512, Maven 3.9.9, MongoDB's
Ubuntu 20.04 package, locally extracted Debian libssl1.1, and cJSON 1.7.17 headers.
No dependencies or compiled bytecode/binaries are shipped in this source overlay.

From FrogNet root, build the unchanged stock broker:

```
make -C opt/frognet_semantic/ribbit/examples/mqtt/stock -j2 WITH_DOCS=no WITH_WEBSOCKETS=no WITH_SQLITE=no WITH_EDITLINE=no WITH_HTTP_API=no DIRS='libcommon lib src client'
```

Set JAVA_HOME and CPATH/LIBRARY_PATH for the listed development headers/libraries.
Then run tools/build_native.sh. The maven.py helper regenerates proxy settings
from the current environment and imports its proxy CA into an isolated truststore.
It installs the exact upstream bundled util JAR with --install-util; no stub is
substituted. Build original before converted checkouts to populate dependencies.

Workspace layout expected by the runners:

- FrogNet-Living-Network: this source tree.
- senzmatica-core: unchanged upstream pin.
- senzmatica-converted: apply tools/apply_backend.py to a fresh upstream worktree.
- senzmatica-converted-repaired: apply with --repair to another fresh worktree.
- baseline-tools: JDK8/Maven/MongoDB paths as recorded in tools/.
- recovery-state: isolated dependency cache and per-run logs.

```
python tools/maven.py --workspace <root> --source <upstream> --state <new-build-state> --install-util
python tools/run.py --workspace <root> --state <new-run-state> --mode original
python tools/check_jni.py --state <new-run-state>
python tools/run.py --workspace <root> --state <new-run-state> --mode ram
python tools/run.py --workspace <root> --state <new-run-state> --mode mixed
python tools/check_mutations.py --workspace <root> --state <new-run-state>
```

All processes run together inside a runner invocation, because separate tool
invocations in this environment isolate loopback networks. Use fresh state paths.
The runners use only local synthetic service credentials/endpoints. They always
save result.json and clean up child processes. Application timing is not inferred
from the oracle's bootstrap/database-observation loops.

## Remaining scope

Real-kernel topology tier added 2026-10-28 (TOPOLOGY.md, tools/topology.py, evidence/topology/):
three network namespaces, silent splits, heal, device/backend/RAM restarts; three runs PASS with
no false success and no repeated physical action. It exposed that sessions do not redial: after a
split longer than the 5 s keepalive a participant stays out until restarted. Rejoin/reassertion
after heal, FrogNet region/RAM-role integration, browser validation, wire/code accounting,
event-based timing and two-machine hardware trials remain.

The JNI checker's "JNI call made without checking exceptions ... CallStaticObjectMethod" warning
is explained: JDK 8's own System.loadLibrary emits it for any native library under -Xcheck:jni,
including an empty one. It is not from the binding.

Reproduced in a second workspace (evidence/workspace-reproduction/): original 7, RAM 13,
mixed 12, JNI 18, mutations 3/3. Tool substitutions are listed in SUBSTITUTIONS.txt there.
Two FAIL results there were the conda MongoDB 5.0.3 shell printing its own log line on stdout;
they are kept, and the wrapper that drops only those lines is described.

Watched JNI sessions are process-lifetime because base Watch has no cancellation;
unsafe disconnect is refused. Runtime reconnect/role rebinding is not implemented.
No speedup, removed-code total or hardware-readiness claim is made.
