# Study: message numbers and the merge (phase 1, step 3)

RangerTrak `b6745f49`: `src/app/domain/msg-no.ts` (164 lines), `src/app/domain/entry-merge.ts` (232), ADR D-68, E-220,
E-218, D-59, E-114. Read, not run; what the code does, then what it means on the memory.

## What stock does

- **The number** (`MsgNo {mission, opPeriod, station, seq}`) is stamped once, when an entry first enters a log, and
  never changed. `nextMsgNo` = 1 + the larger of a stored high-water mark and the highest `seq` already in the log for the
  same op period and station; `bumpHighWater` never lowers the mark, so a deleted last entry's number is not reused.
  `formatMsgNoShort` prints `A007`; `formatMsgNoFull` `26-0142 / OP 2 / A007`; `MSG_NO_CSV_COLUMNS` are the CSV columns.
- **The station letter** is per device; `nextFreeStation` picks the first letter after the highest one seen; a hand-over
  offers *take over station X* or *log alongside as station Y*, because two devices on one letter would count from 1 each.
- **Clashes** (`findMsgNoClashes`, `msgNoClashSentence`): a merge names any incoming number already on a different entry
  here; nothing is renumbered.
- **The merge** (`mergeEntries`): an incoming entry matches by `uid` only (or, for a pre-D-59 packet, `sourceUid`). On a
  match the later `revisedAt` wins, except attachments (union by fingerprint) and `handled` (the later `at` wins). No
  match: appended with a new display `id`, its `uid` kept, `sourceUid` stamped (the 📦 mark). An unnumbered incoming
  entry (a field phone's Report Packet) gets THIS device's next number when appended.

## What it means on the memory

- Every entry is written by the device that logged it, at `(log, <mission>.<opPeriod>, <dev>.<seq>)`. The instance is
  unique by construction, so a clash cannot happen and `findMsgNoClashes` has nothing to find. The `MsgNo` stays in the
  entry's bag exactly as stamped today; the station letter is the device's own fact; `A007` prints unchanged.
- A field phone numbers its own entries with its own station letter at the moment it writes them. Stock numbers them
  on the scribe's device at merge; that changes who stamps a phone's entry, not the number's shape. Decision for
  `QUESTION-3.md`: a phone's entry shows the phone's station letter (it can no longer be "the scribe's next").
- Two devices on the same letter now produce two `A007`s that are different cells. The display stays as it is; whether
  a second device on a letter is refused, warned, or allowed is a `QUESTION-3.md` item (stock prevents it through the
  hand-over dialog only).
- **Revisions**: stock edits an entry in place and the later `revisedAt` wins on merge. With one writer per location the
  device that wrote the entry rewrites its own cell (latest); a correction made on ANOTHER device is a new cell naming
  `revises`, and the reader shows the newest revision. Which of the two the contract needs (stock lets any device
  edit any entry it holds) is a `QUESTION-3.md` item; the `entry-merge` spec retires either way.
- **Handled marks**: the later `at` wins today; as `(handled, <mission>, <entry uid>)` latest, write order decides. The
  difference shows only when two devices mark one entry within the same second.
- **Attachments**: union by fingerprint today; per entry they become cells the writing device owns; a removal is that
  device's write. `QUESTION-3.md`.
- **Gone**: `sourceUid`, the 📦 mark, `uidRemap`, `reporterCredential`/`resolvedUid` (a packet's ranger resolution),
  the high-water map's role in merges (it stays per device: a deleted last entry's number is still not reused).
