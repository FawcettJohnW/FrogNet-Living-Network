# FrogNet site diagrams

Diagrams for the site are **generated from Python**, not hand-drawn, so they
stay consistent with the palette in `site.css` and can be re-rendered whenever
the copy or numbers change.

## Regenerate everything

```
cd diagrams
python3 build.py
```

This writes SVGs into `../assets/diagrams/`. No third-party packages — pure
standard library. Each script also runs on its own, e.g.
`python3 ladder_vs_cliff.py`.

## Files

| Script | Output | Where it's used |
|---|---|---|
| `frognet_svg.py` | — | Shared palette + SVG primitives (mirrors `site.css`) |
| `ladder_vs_cliff.py` | `ladder-vs-cliff.svg` | `the-claim.dc.html` — SotF ladder vs. the UDP cliff |
| `split_and_merge.py` | `split-and-merge.svg` | `why.dc.html` — a pond splits into islands and rejoins |
| `same_diff_full.py` | `same-diff-full.svg` | `products.dc.html` (UnREST) — SAME / DIFF / FULL on the wire |
| `broker_boundary.py` | `broker-boundary.svg` | `products.dc.html` (Broker) — the trust boundary |

## Palette

All colors and fonts live in `frognet_svg.py` (`P`, `DISP`, `SANS`, `MONO`)
and match the "Instrument Dossier" tokens in `site.css`. Change a token there
and re-run `build.py` to update every diagram at once.

## Adding a diagram

1. Write `my_diagram.py` with a `build()` that returns an SVG string via the
   `frognet_svg` helpers.
2. Add it to `DIAGRAMS` in `build.py`.
3. Reference `assets/diagrams/my-diagram.svg` from the page with an `<img>`.
