# v0.43 RED — native registration between Ribbit participants, ETR truth governed in place

Base: v0.42 GREEN (6877d0e8c6374fbd2a8f4138be403332693cb10d91856fc80200b244a303ef11).
Shape from EVAL-v0.41-native-registration.md; the candidate's code is not used.
tools/test_native_registration.py (independent processes, FNW1): two ETRs with identities publish database mappings
and send nothing; a Map-Server governor participant applies site policy; ETRs observe accepted/rejected/withdrawn
decisions; an independent ITR resolves the governed truth, merged per ETR, with per-ETR withdrawal; and no claim
variable exists (no copy of ETR truth). Against v0.42: `unsupported operation: ms_governor.start`
(artifacts/native-registration-red-v0.43.txt). No implementation source changed.
