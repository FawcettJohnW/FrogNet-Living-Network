# frognet::LispHandler -- a C++ UnRESTHandler for LISP

John, 2026-09-25: "Make that a C++ derived class." The design: a LISP handler in the shape of core/unrest_handler.py
and core/sotf_handler.py -- identity + election + lifecycle on the handler, the programs in objects its factories return.

## Files
- ribbit_cpp/unrest_handler.hpp -- the base class, C++ form of core/unrest_handler.py: identity (role_name,
  candidate_type, default_port), codec slot (typed-empty defaults, as the Python: an empty extraction is a real answer),
  election slot (pure: score, evaluate), lifecycle slot (role_scope, advertise, hostReset). advertise() writes Ribbit
  memory (service=<role>, variable=capability, instance=host:<ip>:<role>), NOT the api.php tuple store the Python writes,
  so the Python election does not read it; it takes the capability blob (it does not run the probe) and a failed write
  raises (the Python logs and continues).
- ribbit_cpp/lisp_handler.hpp -- class LispHandler : public UnRESTHandler.
  - identity: role lisp, LispCandidate, port 4342.
  - score(): eligible only if lisp_udp_4342 is true and public_ip is set -- two NEW capability fields that
    frognet_capability_probe.sh does not publish yet. Ranks on static capability (cpu_bench_total / 1000, else
    cores * 0.5), never on load. A present field of the wrong type raises, naming it.
  - evaluate(): the WAN-inclusive hosts_list (the boundary faces the Internet); lan_list unused; tie -> higher IP.
  - codec slot: a mapping record {"iid","eid","ttl","rlocs":[{"address","priority","weight"}]} learns a lisp-mode
    fragment, extracts four fields in that order, rebuilds the record; a body that is not one raises.
  - open_site() -> LispSite: an ETR/ITR on its own Engine; every engine operation through call().
  - open_boundary() -> LispBoundary: its own Engine (one request thread per Engine; participants take no locks; site
    and boundary coordinate through memory) and a UDP socket. Map-Register (type 3) -> wire.register4_notify, Map-Notify
    back to the sender; Map-Request (type 1) -> wire.request4, Map-Reply / negative reply back to the sender.
    A datagram that raises (malformed, unsupported type) is REJECTED: logged with peer, size, type and exception,
    counted (rejected()), and the next datagram is served. A failure of the socket itself raises and stops the thread.
    That per-datagram handling is a decision for John, flagged, not settled: the no-fallbacks alternative -- raise and
    stop -- lets any Internet sender stop the boundary with one packet.
- ribbit_cpp/lisp_engine.hpp -- class Engine and the wire codecs moved VERBATIM out of ribbit_lisp.cpp (which is now its
  main() and the include), so more than one program can hold an Engine. Proof: the concatenation equals the old file
  byte for byte, and the old and new ribbit-lisp binaries disassemble identically (objdump -d; only file metadata differs).
- tools/lisp_handler_driver.cpp, tools/test_lisp_handler.py, tools/bench_lisp_boundary.py; qualify.sh stage "handler".

## Gates corrected in passing (both were vacuous)
- qualify.sh lock / Memory::remove gate grepped ribbit_lisp.cpp only; after the split that is main() and would pass
  vacuously. It now scans ribbit_lisp.cpp, lisp_engine.hpp, lisp_handler.hpp, unrest_handler.hpp (0 and 0).
- package_check.sh's password-serialization gate passed --exclude '*.cpp' '*.hpp' with a named .cpp file; GNU grep
  applies excludes to named files, so it never read one. It now names the participant sources directly; proved by a
  planted leak (exit 1) and the clean tree (PASS). (package_check.sh ignores its argument and checks its own tree.)

## Red / green
- Red (artifacts/lisp-handler-red.txt): every body throws; 12 FAIL (identity passes: it is declarations).
- Green (artifacts/lisp-handler-green.txt): 31 checks PASS against a real RAM server and real UDP -- election, advertise
  read-back, codec round trip and four malformed bodies, a Map-Register over UDP with HMAC-SHA-256 answered by a
  Map-Notify with the same nonce, a bad-HMAC register not answered, a Map-Request answered with the registered RLOC, an
  unregistered EID answered with a negative reply, and a LispSite on a separate Engine resolving the boundary's
  registration through memory.

## Performance (artifacts/performance-lisp-handler.txt; 1-core container, loopback UDP, N=2000)
Map-Request -> Map-Reply (registered) median 29.1 us, p90 36.0; negative reply 18.0 / 20.4;
Map-Register -> Map-Notify 450.8 / 564.4 (the registration is a synchronous RAM write).

## Not in this class yet
IPv6 on the boundary (the engine has register6/request6; the loop dispatches IPv4 only), the mapping-change flag that
would send Map-Notify / SMR to outside xTRs, RLOC probing of outside locators, and publishing the two new capability
fields from frognet_capability_probe.sh.
