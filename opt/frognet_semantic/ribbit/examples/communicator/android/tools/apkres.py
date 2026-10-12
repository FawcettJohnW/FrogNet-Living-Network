#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""Binary AndroidManifest.xml (AXML) and a minimal resources.arsc, written without aapt2 (Google's repositories are
not reachable from the build machine). Formats: frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h.

    apkres.py manifest OUT.axml   -- the Communicator's manifest
    apkres.py arsc OUT.arsc       -- one resource: drawable/icon -> res/drawable/icon.png (id 0x7f010000)
"""
import struct, sys

ANDROID_NS = "http://schemas.android.com/apk/res/android"
# android.R$attr values, read from the framework jar (javap -constants android.R$attr), not typed from memory
ATTR = {"theme": 16842752, "label": 16842753, "icon": 16842754, "name": 16842755, "exported": 16842768,
        "launchMode": 16842781, "screenOrientation": 16842782, "configChanges": 16842783,
        "minSdkVersion": 16843276, "versionCode": 16843291, "versionName": 16843292,
        "windowSoftInputMode": 16843307, "targetSdkVersion": 16843376, "required": 16843406,
        "allowBackup": 16843392, "hardwareAccelerated": 16843475, "resizeableActivity": 16844022}
T_REF, T_STRING, T_DEC, T_HEX, T_BOOL = 0x01, 0x03, 0x10, 0x11, 0x12


def _pool(strings, utf8=False):
    """ResStringPool, UTF-16 (the form every Android release reads)."""
    offs, data = [], b""
    for s in strings:
        offs.append(len(data))
        u = s.encode("utf-16-le")
        n = len(s)
        data += (struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF)) + u + b"\0\0"
    while len(data) % 4:
        data += b"\0"
    hdr = 28
    start = hdr + 4 * len(strings)
    body = struct.pack("<%dI" % len(strings), *offs) + data
    return struct.pack("<HHIIIIII", 0x0001, hdr, hdr + len(body), len(strings), 0, 0, start, 0) + body


class Axml:
    def __init__(self):
        self.attr_names = []          # strings that carry a resource id: first in the pool, in this order
        self.others = []
        self.events = []

    def _s(self, s):
        return s

    def start(self, name, attrs):
        """attrs: list of (ns_or_None, name, kind, value) with kind in 'str','int','hex','bool','ref'."""
        for ns, an, _, _ in attrs:
            if ns == ANDROID_NS and an not in self.attr_names:
                self.attr_names.append(an)
        self.events.append(("start", name, attrs))

    def end(self, name):
        self.events.append(("end", name))

    def build(self):
        strings = list(self.attr_names)
        def idx(s):
            if s not in strings:
                strings.append(s)
            return strings.index(s)
        idx(ANDROID_NS); idx("android")
        for ev in self.events:                        # every string referenced, so the pool is final before writing
            idx(ev[1])
            if ev[0] == "start":
                for ns, an, kind, v in ev[2]:
                    idx(an)
                    if kind == "str":
                        idx(v)
        chunks = []
        resmap = struct.pack("<%dI" % len(self.attr_names), *[ATTR[a] for a in self.attr_names])
        chunks.append(struct.pack("<HHI", 0x0180, 8, 8 + len(resmap)) + resmap)
        chunks.append(struct.pack("<HHIIiii", 0x0100, 16, 24, 1, -1, idx("android"), idx(ANDROID_NS)))
        line = 2
        for ev in self.events:
            if ev[0] == "start":
                _, name, attrs = ev
                # the platform reads attributes sorted by resource id; keep android: ones first, by id
                attrs = sorted(attrs, key=lambda a: (0, ATTR[a[1]]) if a[0] == ANDROID_NS else (1, a[1]))
                ab = b""
                for ns, an, kind, v in attrs:
                    nsi = idx(ANDROID_NS) if ns == ANDROID_NS else -1
                    if kind == "str":
                        raw, dt, data = idx(v), T_STRING, idx(v)
                    elif kind == "int":
                        raw, dt, data = -1, T_DEC, int(v)
                    elif kind == "hex":
                        raw, dt, data = -1, T_HEX, int(v)
                    elif kind == "bool":
                        raw, dt, data = -1, T_BOOL, 0xFFFFFFFF if v else 0
                    elif kind == "ref":
                        raw, dt, data = -1, T_REF, int(v)
                    else:
                        raise ValueError(kind)
                    ab += struct.pack("<iiiHBBI", nsi, idx(an), raw, 8, 0, dt, data & 0xFFFFFFFF)
                ext = struct.pack("<iiHHHHHH", -1, idx(name), 20, 20, len(attrs), 0, 0, 0)
                body = struct.pack("<Ii", line, -1) + ext + ab
                chunks.append(struct.pack("<HHI", 0x0102, 16, 8 + len(body)) + body)
            else:
                chunks.append(struct.pack("<HHIIiii", 0x0103, 16, 24, line, -1, -1, idx(ev[1])))
            line += 1
        chunks.append(struct.pack("<HHIIiii", 0x0101, 16, 24, line, -1, idx("android"), idx(ANDROID_NS)))
        pool = _pool(strings)
        body = pool + b"".join(chunks)
        return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


ICON_ID = 0x7F010000
SCREEN_LANDSCAPE_UNSPEC = -1


def communicator_manifest(version_code, version_name):
    a = Axml()
    A = ANDROID_NS
    a.start("manifest", [(A, "versionCode", "int", version_code), (A, "versionName", "str", version_name),
                         (None, "package", "str", "net.frognet.communicator")])
    a.start("uses-sdk", [(A, "minSdkVersion", "int", 29), (A, "targetSdkVersion", "int", 33)]); a.end("uses-sdk")
    for p in ("android.permission.INTERNET", "android.permission.CAMERA", "android.permission.RECORD_AUDIO",
              "android.permission.MODIFY_AUDIO_SETTINGS", "android.permission.WAKE_LOCK",
              "android.permission.ACCESS_NETWORK_STATE"):
        a.start("uses-permission", [(A, "name", "str", p)]); a.end("uses-permission")
    for f in ("android.hardware.camera", "android.hardware.camera.front", "android.hardware.microphone"):
        a.start("uses-feature", [(A, "name", "str", f), (A, "required", "bool", False)]); a.end("uses-feature")
    a.start("application", [(A, "label", "str", "FrogNet Communicator"), (A, "icon", "ref", ICON_ID),
                            (A, "allowBackup", "bool", False), (A, "hardwareAccelerated", "bool", True)])
    # configChanges: orientation|screenSize|screenLayout|keyboardHidden|smallestScreenSize -- the call survives a turn
    cfg = 0x0080 | 0x0400 | 0x0100 | 0x0020 | 0x0800
    a.start("activity", [(A, "name", "str", "net.frognet.communicator.MainActivity"), (A, "exported", "bool", True),
                         (A, "label", "str", "FrogNet Communicator"), (A, "launchMode", "int", 2),     # singleTask
                         (A, "configChanges", "hex", cfg), (A, "windowSoftInputMode", "hex", 0x10),   # adjustResize
                         (A, "theme", "ref", THEME_ID), (A, "resizeableActivity", "bool", True)])
    a.start("intent-filter", [])
    a.start("action", [(A, "name", "str", "android.intent.action.MAIN")]); a.end("action")
    a.start("category", [(A, "name", "str", "android.intent.category.LAUNCHER")]); a.end("category")
    a.end("intent-filter")
    a.end("activity")
    a.end("application")
    a.end("manifest")
    return a.build()


def arsc_icon():
    """resources.arsc: package 0x7f 'net.frognet.communicator', type 1 'drawable', entry 0 'icon' -> the png path."""
    gpool = _pool(["res/drawable/icon.png"])
    types = _pool(["drawable"])
    keys = _pool(["icon"])
    spec = struct.pack("<HHIBBHI", 0x0202, 16, 20, 1, 0, 0, 1) + struct.pack("<I", 0)
    config = struct.pack("<I", 64) + b"\0" * 60                  # the default configuration
    ty_hdr = 2 + 2 + 4 + 1 + 1 + 2 + 4 + 4 + 64                # 84
    entries = struct.pack("<I", 0)
    entry = struct.pack("<HHI", 8, 0, 0) + struct.pack("<HBBI", 8, 0, T_STRING, 0)
    ty_body = entries + entry
    ty = struct.pack("<HHIBBHII", 0x0201, ty_hdr, ty_hdr + len(ty_body), 1, 0, 0, 1, ty_hdr + len(entries)) + config + ty_body
    pkg_hdr = 288
    name = "net.frognet.communicator".encode("utf-16-le").ljust(256, b"\0")
    type_off = pkg_hdr
    key_off = pkg_hdr + len(types)
    pkg_body = types + keys + spec + ty
    pkg = struct.pack("<HHII", 0x0200, pkg_hdr, pkg_hdr + len(pkg_body), 0x7F) + name + \
        struct.pack("<IIIII", type_off, 1, key_off, 1, 0) + pkg_body
    body = gpool + pkg
    return struct.pack("<HHII", 0x0002, 12, 12 + len(body), 1) + body


THEME_ID = 0  # set from android.R$style by the build (Theme.Material.NoActionBar)

if __name__ == "__main__":
    what, out = sys.argv[1], sys.argv[2]
    if what == "manifest":
        THEME_ID = int(sys.argv[3]); vc = int(sys.argv[4]); vn = sys.argv[5]
        data = communicator_manifest(vc, vn)
    elif what == "arsc":
        data = arsc_icon()
    else:
        raise SystemExit("apkres.py manifest|arsc OUT ...")
    open(out, "wb").write(data)
    print("%s: %d bytes" % (out, len(data)))
