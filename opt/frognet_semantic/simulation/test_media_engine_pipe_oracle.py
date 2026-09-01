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
test_media_engine_pipe_oracle.py - the A/V engine's client viewer must keep stdout a
pure IVF data channel and accept the peer-leg --audio flag.

Fails on the stale fork (opens a file named '-', rejects --audio, leaks banner/telemetry
to stdout). Passes once the four media-path fixes are in. The app's embedded tiles depend
on every one of these:
  - self leg:  --save -  must stream IVF to stdout, NOT open a file literally named '-'
  - any leg :  no human text on stdout (banner + telemetry) or ffmpeg decodes garbage
  - peer leg:  --audio must be accepted (argparse) and must gate audio playback
"""
from __future__ import annotations
import os, sys, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
ENGINE = os.path.join(BUNDLE, "frognet_communicator.py")
sys.path.insert(0, BUNDLE)

FAILS = []
def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if not ok else ""))
    if not ok:
        FAILS.append(label)

def run():
    import frognet_communicator as F

    # 1. --save - -> stdout.buffer, tracked, never a file named '-'
    cwd = os.getcwd(); os.chdir(tempfile.gettempdir())
    try:
        v = F._Viewer(False, "-", None, 10, (320, 240))
        check("--save - maps fh to sys.stdout.buffer", v.fh is sys.stdout.buffer)
        check("_fh_is_stdout flag set", getattr(v, "_fh_is_stdout", False) is True)
        check("no real file named '-' created", not os.path.exists("-"))
        if os.path.exists("-"):
            os.remove("-")
    finally:
        os.chdir(cwd)

    # 2. audio playback gates on --audio, not display: muted leg opens no ffplay
    vm = F._Viewer(False, "-", None, 10, (320, 240), audio=False)
    vm.play_audio(b"\x00\x00")
    check("muted leg (no --audio) opens no audio playback", vm._aproc is None)

    # 3. argparse accepts --audio (exit != 2) and stdout stays clean; banner on stderr
    p = subprocess.run([sys.executable, ENGINE, "--connect", "127.0.0.1:1",
                        "--role", "viewer", "--stream", "s", "--save", "-", "--audio"],
                       capture_output=True)
    check("--audio accepted (no argparse exit 2)", p.returncode != 2,
          f"rc={p.returncode}, err tail={p.stderr.decode('utf-8','replace')[-120:]}")
    check("stdout byte-clean (no banner/telemetry leak)", p.stdout == b"",
          f"{len(p.stdout)} bytes leaked")
    check("'unrecognized arguments: --audio' absent", b"unrecognized arguments" not in p.stderr)
    check("client banner present on stderr", b"app (client)" in p.stderr)

    # 4. capture command must NOT pin -framerate on the input (dshow/v4l2/avfoundation
    #    abort on devices that don't enumerate the rate); rate is capped on output -r.
    class _Cap:
        ffmpeg = "ffmpeg"
        def __init__(self, osn): self.os_name = osn
    for osn in ("Windows", "Darwin", "Linux"):
        cmd = F._live_cmd(_Cap(osn), 10, 120, "video=Some Cam")
        i = cmd.index("-i")
        in_section = cmd[:i]
        out_section = cmd[i:]
        check(f"[{osn}] no -framerate on capture input", "-framerate" not in in_section,
              f"input pins -framerate: {in_section}")
        check(f"[{osn}] output caps rate with -r", "-r" in out_section)

def main():
    print("=== A/V engine pipe + --audio oracle ===")
    try:
        run()
    except Exception as e:
        check("oracle ran without exception", False, repr(e))
    print("\n" + ("ALL MEDIA-ENGINE-PIPE CHECKS PASS" if not FAILS
                  else f"MEDIA-ENGINE-PIPE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
