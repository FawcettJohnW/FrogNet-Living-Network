# v0.23 multi-record Map-Register — RED

- Based directly on verified v0.22 no-semantic-remove green archive.
- Added a non-vacuous two-record IPv4 Map-Register test with mixed /24 and /25 records and distinct RLOCs.
- Existing implementation rejects it before mutation with `wire slice requires one record`.
- No implementation source changed in this checkpoint.
