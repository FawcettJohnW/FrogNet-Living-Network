# v0.22 no semantic remove — GREEN

- v0.21 held map-resolver retained.
- database_mapping.delete now publishes registered:false current state instead of physical removal.
- removed unused ram_delete/ram_mappings helpers.
- removed unreachable RAM-reading branch from governance_for; RAM resolution uses held governance.
- zero Memory::remove calls remain in ribbit_lisp.cpp.
- remaining direct reads classified in AUDIT-v0.22.md.
- Local conformance: 31 PASS, 1 explicit SKIP.
- Clean FNW1 conformance: 31 PASS, 1 explicit SKIP.
- Dino-derived saved byte fixtures unchanged; no oracle fixture/source modified.
