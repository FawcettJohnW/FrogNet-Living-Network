#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
agent_workload/experiment/capture.py -- what happened, kept exactly as it
happened.

[THE_RAW_RECORD_IS_NEVER_REWRITTEN_V1]

The pipeline is

    execution -> capture -> post-processor -> report

and capture's whole job is to be the part that cannot lie later. It stores
the run's immutable metadata and the lifecycle events exactly as the policy
layer emitted them. It derives nothing, normalises nothing, and repairs
nothing.

That matters for one specific reason: if the post-processor turns out to have
a bug -- and today alone produced a summed maximum, a participation count
that included self, an acceptance decision recorded as a departure and a
ratio against an arm that finished half its workers -- the experiment must
not have to be re-run in order to be reinterpreted. Every one of those was
found after the fact, and every one of them would have been unfixable if the
only surviving artefact had been the interpretation.

So:

  * `run.json` is written once and refuses to be written again.
  * `events.jsonl` is opened for append and never rewritten.
  * an event is stored verbatim, inside an envelope that adds context
    ALONGSIDE it rather than mutating it.
  * the schema version travels with the data, so a later reader knows what
    it is looking at instead of guessing.

Nothing here imports torch, so the post-processor and the oracles can read a
run without the tensor stack.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

#: Bumped when the ENVELOPE changes. The event payload inside it is whatever
#: the emitting layer produced; readers must tolerate fields they do not know.
SCHEMA_VERSION = 1

RUN_FILE = "run.json"
EVENT_FILE = "events.jsonl"


class CaptureError(RuntimeError):
    pass


class RunCapture:
    """One run's directory. Metadata once, events appended, nothing edited."""

    def __init__(self, root: str, run_id: str, meta: Optional[Dict] = None,
                 create: bool = True):
        self.root = os.path.join(root, run_id)
        self.run_id = run_id
        self._path_run = os.path.join(self.root, RUN_FILE)
        self._path_ev = os.path.join(self.root, EVENT_FILE)
        self._seq = 0
        if create:
            os.makedirs(self.root, exist_ok=True)
            self._write_meta(meta or {})

    def _write_meta(self, meta: Dict) -> None:
        # [THE_RAW_RECORD_IS_NEVER_REWRITTEN_V1] A run specification that can
        # be edited after the run is not a specification, it is a note. If
        # something about the run changed, that is a new run with a parent.
        if os.path.exists(self._path_run):
            raise CaptureError(
                "%s already exists. A run's metadata is written once; a run "
                "that needs different metadata is a different run, with this "
                "one as its parent." % self._path_run)
        doc = {"schema": SCHEMA_VERSION, "run_id": self.run_id,
               "created": time.time(), "meta": meta}
        fd = os.open(self._path_run, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, sort_keys=True)
            f.write("\n")

    def append(self, event: Dict[str, Any], /, **context) -> int:
        # `event` is positional-only so a caller passing event=... lands in
        # context and hits the shadowing guard below, rather than being
        # rejected by Python with a message about duplicate arguments.
        """Store one event verbatim, with context beside it.

        The event goes in under "event" untouched -- not merged into the
        envelope, not renamed, not filled in. A reader can always recover
        exactly what the emitting layer said.
        """
        if not isinstance(event, dict):
            raise CaptureError("an event must be a dict, got %r"
                               % type(event).__name__)
        env = {"schema": SCHEMA_VERSION, "seq": self._seq,
               "t": round(time.time(), 6), "event": event}
        for k, v in context.items():
            if k in ("event", "seq", "schema"):
                raise CaptureError(
                    "context key %r would shadow the envelope; context sits "
                    "BESIDE the event, it does not overwrite it" % k)
            env[k] = v
        with open(self._path_ev, "a") as f:
            f.write(json.dumps(env, sort_keys=True, default=str))
            f.write("\n")
        self._seq += 1
        return env["seq"]

    def append_many(self, events: Iterable[Dict[str, Any]], /, **context)\
            -> List[int]:
        return [self.append(e, **context) for e in events]

    def close(self) -> str:
        return self.root


def read_run(path: str) -> Dict[str, Any]:
    with open(os.path.join(path, RUN_FILE)) as f:
        return json.load(f)


def read_events(path: str) -> Iterator[Dict[str, Any]]:
    """Every envelope, in the order it was appended.

    A truncated final line is reported rather than skipped: a run that was
    killed mid-write is a fact about the run, and silently dropping the
    remainder would make an incomplete capture look complete.
    """
    p = os.path.join(path, EVENT_FILE)
    if not os.path.exists(p):
        return
    with open(p) as f:
        for n, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as e:
                raise CaptureError(
                    "%s line %d is not readable: %s. The capture was "
                    "interrupted; the events before this point are still "
                    "valid and this one is not." % (p, n + 1, e))
