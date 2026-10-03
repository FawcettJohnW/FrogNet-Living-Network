# FrogNet Communicator

Presence, a lobby, chat, and live audio and video calls over FrogNet shared memory (Ribbit), through a media server
that serves each viewer at its own rate. Two implementations of one specification, interoperating on the same wire:

- **C++** (`src/`): the window (`comms-app`, Qt), the unattended feed (`comms-feed`), the media server (`comms-media`)
  and the memory (`comms-ram`). Linux, Raspberry Pi, Windows (Visual Studio or MSYS2).
- **Python** (`python/`): the window (`comms_app.py`, PySide6) and the feed (`comms_feed.py`); see `python/README.md`.

Self-contained: the Ribbit platform it compiles is vendored in `third_party/ribbit`.

| | |
|---|---|
| build (Linux, Pi) | `./build.sh` |
| build (Windows) | `powershell -ExecutionPolicy Bypass -File .\build-windows.ps1` -- see `WINDOWS.md` |
| run and test | `TESTING.md` |
| what is built and proven | `STATUS.md` |
| the specifications | `docs/COMMUNICATOR-SPEC.md`, `docs/FNAV-SPEC.md` |

Copyright (C) 2016-2026 Fawcett Innovations LLC. GPL-2.0-only.
