# Ribbit

Ribbit is the programming model for distributed applications written as shared state in Internet RAM. An application
determines its truth, writes it, reads the other truths, and goes: there is no server logic between participants, no
messages to exchange and no copies to keep in agreement. Four lines of description; the rest of this tree is what it
takes to do that for real applications, on the open Internet, today.

A vendor's application has two halves, both built from this tree:

- **its RAM host** -- `<vendor>-ram`, one executable: the memory (cells addressed by service, variable and instance;
  row locks and no other lock; held reads), FNW1 (FrogNet's semantic wire) to its participants, and the vendor's own
  region compiled in. Its user runs it, naming an address and port if the default does not suit. Nothing else.
- **its client library** -- the vendor's API as ordinary C++ calls, built as a shared library with a plain C interface
  so any language can load it. Every call is one semantically compressed request over the session's dedicated
  socket to the vendor's RAM host.

**Start with [`docs/RIBBIT.md`](docs/RIBBIT.md)**: what Ribbit is and why it matters (Part I), how to program in it
(Part II), how it optimizes communications (Part III), debugging and logging (Part IV), building and deploying
(Part V), and using AI for maximum code velocity (Part VI).

## The tree

| directory | what it is |
|---|---|
| [`cpp/`](cpp/README.md) | the platform: `ribbit::RamHost` and `ribbit::RamClient` (the two base classes a vendor derives from), the memory, FNW1, the client library core, and the platform's own qualification |
| [`defaults/`](defaults/README.md) | the default API and schema: the tuples FrogNet discovery uses, as a client library (`libtuples`), and the Python module applications written for a FrogNet node import |
| [`docs/`](docs/README.md) | `RIBBIT.md`, the document to start with, and the platform's design |
| [`examples/`](examples/README.md) | four applications, each built stand-alone and runnable from the Internet: chat, lispers.net, the Communicator, Redis |

## Building

Every example builds itself against `cpp/` (and `defaults/` where it uses the default API): see each example's
README. The platform alone: `cpp/qualify.sh`.

Needs: Linux, `g++` 11 or later, `liblz4-dev`, `libssl-dev`, `python3`. Each example names anything more it needs.
