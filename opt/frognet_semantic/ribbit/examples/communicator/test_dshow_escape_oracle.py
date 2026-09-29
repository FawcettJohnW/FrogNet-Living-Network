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
"""test_dshow_escape_oracle.py - dshow device names with specials are escaped so ffmpeg
doesn't reject them with 'Malformed dshow input string'. Regression for the C920 mic bug."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frognet_communicator as F

_p=_f=0
def ck(n,c,x=""):
    global _p,_f
    if c:_p+=1;print(f"  [PASS] {n}")
    else:_f+=1;print(f"  [FAIL] {n}  {x}")

e=F._dshow_escape
print("=== dshow device-name escaping ===")
# the exact device that broke on John's box
out=e("audio=Microphone (HD Pro Webcam C920)")
ck("D1 parens escaped", "\\(" in out and "\\)" in out, out)
ck("D1 prefix preserved", out.startswith("audio="), out)
ck("D2 plain name (no specials) unchanged", e("video=USB Video Device")=="video=USB Video Device")
ck("D3 colon escaped", e("video=Cam (046d:082d)")=="video=Cam \\(046d\\:082d\\)", e("video=Cam (046d:082d)"))
ck("D4 non-dshow spec (no =) untouched", e("hw:0,0")=="hw:0,0")
ck("D5 backslash escaped first (no double-escape)", e("audio=a\\b").count("\\")==2, e("audio=a\\b"))
print(f"\n=== {_p} passed, {_f} failed ===")
sys.exit(1 if _f else 0)
