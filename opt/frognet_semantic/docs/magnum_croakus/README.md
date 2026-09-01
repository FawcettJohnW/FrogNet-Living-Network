# Magnum Croakus — build kit (partial)

Source of truth for *Magnum Croakus — How to Work Like a Frog*.
**Edit `build_magnum.js`, NEVER the generated `.docx`.**

## Present here
- `build_magnum.js` — the generator (Node + `docx`). ~37 sections, 6 appendices.
  Verified this rev: parses and runs; builds a valid docx once the figures exist.

## NOT here yet (upload to complete the kit)
- `make_magnum_figures.py` — regenerates the four embedded PNGs (matplotlib).
- `magnum-figures/*.png` — the embedded figures. `build_magnum.js` reads them at
  build time (e.g. `magnum-figures/multi_transport.png`); without them the build
  stops at ENOENT. The figures are RECONSTRUCTIONS of lost originals — verify each
  against its section.

## Build (once the figures are present)
    python3 make_magnum_figures.py     # only if figures missing/changed
    npm install docx                   # once
    node build_magnum.js               # writes Magnum_Croakus.docx

## Checked against the 2026-07-20 discovery/loop fixes
No book change needed. Appendix E ("Advanced Simulation — The Disaster Area") and
§12 assert reachability / component-count / routing STRUCTURE, not specific /32
tables — exactly the invariants the loop fix preserves. Ran Appendix E's own 8-cell
topology (LAN_SIZES=[3,4,5,4,3,5,4,3], 31 nodes) against the fixed sim: converged,
0 back-vouch loops, full reachability. The loop prose (Parts III/IX: "children do
not advertise up") describes the structural rule the counter+memo backstops — not
contradicted.
