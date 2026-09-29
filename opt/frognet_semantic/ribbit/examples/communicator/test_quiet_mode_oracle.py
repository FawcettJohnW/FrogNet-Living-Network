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
test_quiet_mode_oracle.py -- drives the REAL comms_voice.QuietMode headless.

Speech engines and the control plane are stubs; everything between them is shipping
code. Two nodes share one transcript store, so this also proves the thing the mode
exists to demonstrate: the conversation is a place both ends read, not a stream one
end sends.

Run: python3 test_quiet_mode_oracle.py
"""
import sys
import time

import comms_voice as V

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


# -- stubs -------------------------------------------------------------------
class Store:
    """One shared transcript, as the tuple plane would hold it."""

    def __init__(self):
        self.rows = []


class StubCP:
    def __init__(self, store, me_id, me_name):
        self.store, self.me_id, self.me_name = store, me_id, me_name
        self._clock = 1000

    def say(self, session, text, kind="typed", speaker=""):
        self._clock += 1
        self.store.rows.append({"session": session, "text": text, "kind": kind,
                                "from_id": self.me_id,
                                "from": speaker or self.me_name,
                                "ts": self._clock})
        return True

    def read_transcript(self, session, fresh_s=3600):
        return sorted([r for r in self.store.rows if r["session"] == session],
                      key=lambda r: r["ts"])


class StubStt(V.SttEngine):
    name = "stub"

    def __init__(self, on=True):
        self.on, self.fed, self.queued = on, bytearray(), []

    def available(self):
        return self.on

    def feed(self, pcm):
        self.fed.extend(pcm)

    def take(self):
        out, self.queued = self.queued, []
        return out


class StubTts(V.TtsEngine):
    name = "stub"

    def __init__(self, on=True):
        self.on, self.spoken = on, []

    def available(self):
        return self.on

    def say(self, text):
        self.spoken.append(text)


class StubCall:
    def __init__(self):
        self.cap = None
        self.mute_mic = False
        self.mute_speaker = False
        self.audio_tap = None

    def set_level_cap(self, c):
        self.cap = c


# =============================================================================
print("-- engaging quiet mode uses the ladder, not a new mechanism ------------")
store = Store()
john = V.QuietMode(StubCP(store, "john", "John"), "s1", "john",
                   stt=StubStt(), tts=StubTts())
call = StubCall()
john.engage(call)
ck("the microphone is muted", call.mute_mic is True)
ck("the speaker is silenced", call.mute_speaker is True)
ck("video is blanked by capping the ladder below the video rungs", call.cap == 4)
ck("received audio is tapped for transcription", call.audio_tap is not None)

john.release(call)
ck("leaving restores the microphone", call.mute_mic is False)
ck("leaving restores the speaker", call.mute_speaker is False)
ck("leaving lifts the cap back to the top rung", call.cap == 7)
ck("and removes the tap", call.audio_tap is None)
john.engage(call)


# =============================================================================
print("-- typing lands in shared memory, not in a message ---------------------")
dan_store_cp = StubCP(store, "dan", "Dan")
dan = V.QuietMode(dan_store_cp, "s1", "dan", stt=StubStt(), tts=StubTts())

john.send("can you read this")
ck("the line is in the store", len(store.rows) == 1)
ck("marked as typed", store.rows[0]["kind"] == "typed")

fresh = dan.collect()
ck("the far side reads it out of shared memory", len(fresh) == 1)
ck("with the text intact", fresh[0]["text"] == "can you read this")
ck("and speaks it", dan.tts.spoken == ["can you read this"])
ck("the writer is not read his own words back",
   john.collect() and john.tts.spoken == [])

ck("re-reading the transcript does not speak anything twice",
   dan.collect() == [] and dan.tts.spoken == ["can you read this"])

john.send("   ")
ck("an empty line is not written", len(store.rows) == 1)


# =============================================================================
print("-- a latecomer reads the whole conversation -----------------------------")
dan.send("loud and clear")
john.send("good")
julie = V.QuietMode(StubCP(store, "julie", "Julie"), "s1", "julie",
                    stt=StubStt(), tts=StubTts())
seen = julie.collect()
ck("someone who joins late gets everything said so far",
   [l["text"] for l in seen] == ["can you read this", "loud and clear", "good"],
   [l["text"] for l in seen])
ck("which a fan-out over the media socket could not have given her",
   len(seen) == 3)


# =============================================================================
print("-- incoming speech becomes a transcript line ---------------------------")
store2 = Store()
cp2 = StubCP(store2, "john", "John")
stt = StubStt()
solo = V.QuietMode(cp2, "s2", "john", stt=stt, tts=StubTts())
call2 = StubCall()
solo.engage(call2)

# fnav's receive loop hands the tap 16 kHz mono int16, before the mixer.
block = b"\x00\x01" * 400
for _ in range(30):
    call2.audio_tap("Dan", block)
ck("audio reaches the recogniser", len(stt.fed) > 0)
ck("it is fed in chunks, not one sample at a time",
   len(stt.fed) >= V.AUDIO_RATE, len(stt.fed))

stt.queued = ["the pond is frozen"]
lines = solo.pump()
ck("what was heard is published", len(store2.rows) == 1)
ck("marked as heard, not typed", store2.rows[0]["kind"] == "heard")
ck("and comes back as a line", lines and lines[0]["text"] == "the pond is frozen")

heard = {"kind": "heard", "from_id": "dan", "text": "hello"}
ck("a transcription is never spoken back at the person who muted the speaker",
   solo.should_speak(heard) is False)


# =============================================================================
print("-- with no engines at all the mode still works -------------------------")
store3 = Store()
bare = V.QuietMode(StubCP(store3, "john", "John"), "s3", "john",
                   stt=V.NullStt(), tts=V.NullTts())
bare_call = StubCall()
bare.engage(bare_call)
ck("engaging still mutes the box", bare_call.mute_mic and bare_call.mute_speaker)
ck("typing still works", bare.send("text floor is a complete conversation") is True)
ck("and the line is in shared memory", len(store3.rows) == 1)
call3 = bare_call
call3.audio_tap("Dan", b"\x00\x01" * 400)
ck("audio with no recogniser is discarded, not buffered forever",
   len(bare._pcm) == 0)
ck("nothing is spoken", bare.should_speak({"kind": "typed", "from_id": "dan",
                                           "text": "hi"}) is False)
ck("the status says what is missing, not that it is broken",
   "install" in bare.status() or "no speech" in bare.status(), bare.status())

ck("a probe returns an engine object either way",
   isinstance(V.probe_stt(), V.SttEngine) and isinstance(V.probe_tts(), V.TtsEngine))


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
