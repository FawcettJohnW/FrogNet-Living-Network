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
comms_voice.py -- QUIET MODE. Camera blanked, microphone muted, speaker silenced;
you read what the far side says and type what you want said.

This is the SotF ladder's text floor made usable by a person. The ladder has always
said that when a link cannot carry pictures you send audio, and when it cannot carry
audio you send text -- L2 WHISPER, "plaintext on the wire". Quiet mode is that same
step taken for a reason that is not bandwidth: you are somewhere you cannot talk, or
cannot be seen, or cannot make noise. The medium changes; the call does not end.

HOW IT WORKS, AND WHERE THE STATE LIVES

  incoming speech -> STT -> a line in the call's SHARED TRANSCRIPT
  what you type   ->        a line in the call's SHARED TRANSCRIPT -> TTS on every
                                                                     other box

Nothing is sent to anybody. Text is written into memory the call shares, and each
participant reads it and renders it however it can: on screen, or out loud through
whatever speech engine that box happens to have. That asymmetry is the point. A Pi
with espeak and a laptop with piper produce different voices from the same line of
memory, and a node that joins ten minutes late reads the whole conversation, because
the conversation is a place rather than a stream. Had this been fanned out over the
media socket instead, the latecomer would get nothing and a node that blinked would
lose the line for good.

ENGINES ARE OPTIONAL AND PROBED, NEVER ASSUMED

There is no bundled speech engine and this file does not pretend otherwise. It looks
for what is installed, reports exactly what it found, and says what to install if it
found nothing. With no engines at all quiet mode still works -- typing and reading is
a complete conversation at the text floor, which is precisely what L2 promises. STT
adds hearing; TTS adds a voice. Neither is required for the mode to be useful.

ASCII only.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

AUDIO_RATE = 16000          # fnav's wire rate; the tap hands us mono int16 at this


# =============================================================================
# speech to text
# =============================================================================
class SttEngine:
    """Base: feed() PCM, get finished lines back from take()."""

    name = "none"
    install_hint = ""

    def available(self) -> bool:
        return False

    def feed(self, pcm: bytes) -> None:
        pass

    def take(self) -> List[str]:
        return []

    def close(self) -> None:
        pass


class VoskStt(SttEngine):
    """Offline, small, runs on a Pi, streams -- the right shape for a live call.

    Vosk wants exactly what the tap provides: 16 kHz mono int16. No resampling, no
    buffering games, no round trip to anybody's server.
    """

    name = "vosk"
    install_hint = ("pip install vosk, then put a model in "
                    "~/.cache/vosk or set VOSK_MODEL")

    def __init__(self, model_path: Optional[str] = None):
        self._rec = None
        self._lines: List[str] = []
        self._lock = threading.Lock()
        try:
            import vosk
            vosk.SetLogLevel(-1)
            path = model_path or os.environ.get("VOSK_MODEL") or self._find_model()
            if not path:
                return
            self._model = vosk.Model(path)
            self._rec = vosk.KaldiRecognizer(self._model, AUDIO_RATE)
        except Exception:
            self._rec = None

    @staticmethod
    def _find_model() -> Optional[str]:
        for base in (os.path.expanduser("~/.cache/vosk"),
                     "/usr/share/vosk", "/opt/vosk"):
            if os.path.isdir(base):
                for entry in sorted(os.listdir(base)):
                    full = os.path.join(base, entry)
                    if os.path.isdir(full) and os.path.isdir(os.path.join(full, "am")):
                        return full
        return None

    def available(self) -> bool:
        return self._rec is not None

    def feed(self, pcm: bytes) -> None:
        if self._rec is None:
            return
        try:
            if self._rec.AcceptWaveform(pcm):
                text = json.loads(self._rec.Result()).get("text", "").strip()
                if text:
                    with self._lock:
                        self._lines.append(text)
        except Exception:
            pass

    def take(self) -> List[str]:
        with self._lock:
            out, self._lines = self._lines, []
        return out


class NullStt(SttEngine):
    name = "none"
    install_hint = "no speech recognition installed -- typing still works"


def probe_stt() -> SttEngine:
    e = VoskStt()
    return e if e.available() else NullStt()


# =============================================================================
# text to speech
# =============================================================================
class TtsEngine:
    name = "none"
    install_hint = ""

    def available(self) -> bool:
        return False

    def say(self, text: str) -> None:
        pass


class CommandTts(TtsEngine):
    """espeak-ng or piper: a binary that turns text into sound on this box.

    Spoken in a worker thread. Speech is slower than reading, so a burst of lines
    must queue rather than overlap or block the UI; a queue that has fallen behind
    is drained to the newest line, because in a live conversation the current line
    matters and a backlog of stale ones does not.
    """

    def __init__(self, argv, name, hint):
        self.argv, self.name, self.install_hint = argv, name, hint
        self._q: "queue.Queue[str]" = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        if self.available():
            threading.Thread(target=self._worker, daemon=True).start()

    def available(self) -> bool:
        return bool(self.argv) and shutil.which(self.argv[0]) is not None

    def say(self, text: str) -> None:
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass

    def _worker(self):
        while not self._stop.is_set():
            try:
                text = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            while self._q.qsize() > 3:          # behind: keep the newest
                try:
                    text = self._q.get_nowait()
                except queue.Empty:
                    break
            try:
                subprocess.run(self.argv + [text], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30)
            except Exception:
                pass

    def close(self):
        self._stop.set()


class Pyttsx3Tts(TtsEngine):
    name = "pyttsx3"
    install_hint = "pip install pyttsx3"

    def __init__(self):
        self._engine = None
        self._q: "queue.Queue[str]" = queue.Queue(maxsize=32)
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
        except Exception:
            self._engine = None
        if self._engine is not None:
            threading.Thread(target=self._worker, daemon=True).start()

    def available(self) -> bool:
        return self._engine is not None

    def say(self, text: str) -> None:
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass

    def _worker(self):
        while True:
            text = self._q.get()
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                pass


class NullTts(TtsEngine):
    name = "none"
    install_hint = ("no speech synthesis installed -- apt install espeak-ng, "
                    "or pip install pyttsx3")


def probe_tts() -> TtsEngine:
    for engine in (CommandTts(["espeak-ng"], "espeak-ng", "apt install espeak-ng"),
                   CommandTts(["espeak"], "espeak", "apt install espeak"),
                   CommandTts(["piper", "--output_raw"], "piper",
                              "install piper-tts and a voice")):
        if engine.available():
            return engine
    p = Pyttsx3Tts()
    return p if p.available() else NullTts()


# =============================================================================
# the mode itself
# =============================================================================
class QuietMode:
    """Drives quiet mode for one call: mutes the box, transcribes what comes in,
    speaks what arrives from others, and publishes both into shared memory.

    The shell owns the window; this owns the behaviour, so it can be driven headless
    by an oracle with stub engines and a stub control plane.
    """

    def __init__(self, cp, session, me_id, stt=None, tts=None, speak_own=False):
        self.cp = cp
        self.session = session
        self.me_id = me_id
        self.stt = stt if stt is not None else probe_stt()
        self.tts = tts if tts is not None else probe_tts()
        self.speak_own = speak_own      # normally False: you do not need reading back
        self.seen: set = set()
        self.on_line = None             # callback(line dict) for the window
        self._pcm = bytearray()
        self._last_flush = time.time()

    # -- state of the call ------------------------------------------------
    def engage(self, call) -> None:
        """Blank the camera, mute the microphone, silence the speaker.

        Video off is the ladder's own mechanism, not a new one: capping the level at
        L4 removes every video rung from `allowed`, so the TX loop's next frame walks
        down to an audio rung and stops encoding. Nothing is torn down and nothing
        has to be rebuilt when the cap is lifted."""
        if call is None:
            return
        call.set_level_cap(4)
        call.mute_mic = True
        call.mute_speaker = True
        call.audio_tap = self._on_audio

    def release(self, call) -> None:
        if call is None:
            return
        call.audio_tap = None
        call.mute_mic = False
        call.mute_speaker = False
        call.set_level_cap(7)

    # -- incoming speech --------------------------------------------------
    def _on_audio(self, src, pcm) -> None:
        """Called from fnav's receive loop. Do as little as possible here: this
        thread is the one reading frames off the socket."""
        if not self.stt.available():
            return
        self._pcm.extend(pcm)
        if len(self._pcm) >= AUDIO_RATE:          # ~0.5 s of 16-bit mono
            chunk = bytes(self._pcm)
            del self._pcm[:]
            self.stt.feed(chunk)

    def pump(self) -> List[Dict[str, Any]]:
        """Call from the UI tick. Publishes anything the recogniser finished, then
        returns transcript lines that are new to this node."""
        for text in self.stt.take():
            try:
                self.cp.say(self.session, text, kind="heard")
            except Exception:
                pass
        return self.collect()

    def collect(self) -> List[Dict[str, Any]]:
        """Read the shared transcript and hand back what we have not seen.

        Deduplication is by (ts, from_id, text) and is a display concern only: it
        stops the same line being spoken twice on this box. It is not arbitration --
        nothing here decides what the transcript IS. The transcript is whatever the
        rows say.
        """
        try:
            lines = self.cp.read_transcript(self.session)
        except Exception:
            return []
        fresh = []
        for line in lines:
            key = (line.get("ts"), line.get("from_id"), line.get("text"))
            if key in self.seen:
                continue
            self.seen.add(key)
            fresh.append(line)
            if self.should_speak(line):
                self.tts.say(line["text"])
            if self.on_line:
                try:
                    self.on_line(line)
                except Exception:
                    pass
        return fresh

    def should_speak(self, line) -> bool:
        """Speak what other people typed. Do not speak transcriptions: a line marked
        'heard' is already somebody's voice that this box could not play out loud,
        and reading it back in a synthetic voice would be a strange thing to do to
        the person who muted the speaker on purpose."""
        if line.get("kind") != "typed":
            return False
        if line.get("from_id") == self.me_id and not self.speak_own:
            return False
        return self.tts.available()

    # -- outgoing ---------------------------------------------------------
    def send(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        try:
            self.cp.say(self.session, text, kind="typed")
            return True
        except Exception:
            return False

    def status(self) -> str:
        parts = []
        parts.append("hearing: %s" % (self.stt.name if self.stt.available()
                                      else self.stt.install_hint))
        parts.append("voice: %s" % (self.tts.name if self.tts.available()
                                    else self.tts.install_hint))
        return "    ".join(parts)
