# v0.44 RED — native registrations must die with their ETR

Base: v0.43 GREEN (52f4d7ae38c767a0a3763ab2a59edd6afac3a92911a19a0f2800d532b9373e88).
tools/test_native_liveness.py: three ETRs, two publishing liveness; the one that never published liveness must not
resolve; after one ETR process is killed its contribution must stop resolving (nothing written) while the other stays;
after a clean heartbeat stop the same. Against v0.43: `unsupported operation: etr_liveness.start`
(artifacts/native-liveness-red-v0.44.txt); the evaluation (EVAL-v0.41, Finding 3) had shown v0.43's shape resolving
a killed ETR's registration indefinitely. No implementation source changed.
