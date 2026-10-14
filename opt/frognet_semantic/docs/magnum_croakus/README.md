# Magnum Croakus — generator

Builds `Magnum_Croakus.docx` and `Magnum Croakus.html` from one source, `build_magnum.js`.
This is the 2026-10-11 edition: the 10-07 audited edition (published book rebased with architecture checkpoint 6,
Part XV1/2, and the normative audit), then checked section by section against the 2026-10-11 code and corrected
to match it (notes/VERIFY-2026-10-11.md).

## Build
    npm install          # docx 9.10.0, pinned in package.json / package-lock.json
    node build_magnum.js # writes both outputs here

Node 22 was used. The HTML output must be published next to `magnum-figures/`.

## Files
- `build_magnum.js` — the generator. Edit this, never the outputs.
- `shell_head.html` — HTML head and stylesheet; the generator reads it at build time.
- `magnum-figures/` — all 32 figures the generator references, including the frontispiece.
- `notes/REVIEW-2026-10-07.md` — what the rebase and audit changed.
- `notes/MERGE-DROPPED.md` — published wording that lost to checkpoint wording in the merge.
- `notes/AUDIT-normative-2026-10-07.md` — the normative-consistency audit.
- `notes/VERIFY-2026-10-08.md` — the 10-08 verification against the 3 Oct code (superseded by 10-11).
- `notes/VERIFY-2026-10-11.md` — verification against the 10-11 code: 347 problems found, then an independent
  second pass over the corrected book found 72 more. All applied.
- `notes/OPEN-FOR-JOHN-2026-10-11.md` — the code questions and rulings the check raised (the book states what the code does).
- `notes/patches-2026-10-11/` — every edit, as scripts, in the order applied: audit_1008_a, audit_1008_b, A..K, S,
  R-12, R-34, R-56, T. Each asserts its text matches exactly once.

## Appendix L (doctrine index)
Generated at build time by scanning a FrogNet source tree for `[TAG_Vn]` markers.
Set `FROGNET_SRC` to that tree; without it the appendix prints its "not generated" notice.
