#!/usr/bin/env python3
# axml.py -- writes FrogNet Kart's AndroidManifest.xml in Android's binary XML (what aapt would write), with no Android
# SDK: Google's Maven (aapt2) is not reachable from where this is built. The framework attribute and style ids are read
# from the android-all jar (javap) by build_apk.sh and passed in, never typed here. [KART_ANDROID_V1]
#   axml.py OUT.xml VERSION_CODE VERSION_NAME ID_FILE
# ID_FILE: lines "name value" for the attributes used below and Theme_Material_NoActionBar_Fullscreen.
import struct, sys

ANDROID = "http://schemas.android.com/apk/res/android"
T_REF, T_STRING, T_DEC, T_HEX, T_BOOL = 0x01, 0x03, 0x10, 0x11, 0x12

def manifest(vcode, vname, theme):
    A = lambda n, t, v: (ANDROID, n, t, v)
    return ("manifest", [A("versionCode", T_DEC, vcode), A("versionName", T_STRING, vname), (None, "package", T_STRING, "net.frognet.kart")], [
        ("uses-sdk", [A("minSdkVersion", T_DEC, 26), A("targetSdkVersion", T_DEC, 33)], []),
        ("uses-permission", [A("name", T_STRING, "android.permission.INTERNET")], []),
        ("application", [A("label", T_STRING, "FrogNet Kart"), A("theme", T_REF, theme), A("hardwareAccelerated", T_BOOL, True)], [
            ("activity", [A("name", T_STRING, "net.frognet.kart.MainActivity"), A("label", T_STRING, "FrogNet Kart"),
                          A("exported", T_BOOL, True), A("screenOrientation", T_DEC, 6),          # 6 = sensorLandscape
                          A("configChanges", T_HEX, 0x0DA0)], [                                   # orientation|keyboardHidden|screenSize|screenLayout|smallestScreenSize
                ("intent-filter", [], [
                    ("action", [A("name", T_STRING, "android.intent.action.MAIN")], []),
                    ("category", [A("name", T_STRING, "android.intent.category.LAUNCHER")], []),
                ]),
            ]),
        ]),
    ])

def build(root, ids):
    # string pool: every attribute name that has a resource id first (in resource-map order), then everything else
    attr_names = []
    def walk_attrs(e):
        for ns, n, t, v in e[1]:
            if ns == ANDROID and n not in attr_names: attr_names.append(n)
        for c in e[2]: walk_attrs(c)
    walk_attrs(root)
    attr_names.sort(key=lambda n: ids[n])
    strings = list(attr_names)
    def s(x):
        if x not in strings: strings.append(x)
        return strings.index(x)
    s("android"); s(ANDROID)
    def walk_strings(e):
        s(e[0])
        for ns, n, t, v in e[1]:
            s(n)
            if t == T_STRING: s(v)
        for c in e[2]: walk_strings(c)
    walk_strings(root)

    body = b""
    def chunk(typ, hsize, payload_head, payload=b""):
        size = 8 + len(payload_head) + len(payload)
        return struct.pack("<HHI", typ, hsize, size) + payload_head + payload
    line = [1]
    def element(e):
        nonlocal body
        name, attrs, kids = e
        def key(a):
            ns, n, t, v = a
            return (0, ids[n]) if ns == ANDROID else (1, 0)
        attrs = sorted(attrs, key=key)
        ab = b""
        for ns, n, t, v in attrs:
            nsi = s(ns) if ns else 0xFFFFFFFF
            if t == T_STRING: raw, data = s(v), s(v)
            elif t == T_BOOL: raw, data = 0xFFFFFFFF, 0xFFFFFFFF if v else 0
            else: raw, data = 0xFFFFFFFF, int(v)
            ab += struct.pack("<IIIHBBI", nsi, s(n), raw, 8, 0, t, data)
        ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, s(name), 20, 20, len(attrs), 0, 0, 0)
        body += chunk(0x0102, 16, struct.pack("<II", line[0], 0xFFFFFFFF), ext + ab); line[0] += 1
        for k in kids: element(k)
        body += chunk(0x0103, 16, struct.pack("<II", line[0], 0xFFFFFFFF), struct.pack("<II", 0xFFFFFFFF, s(name))); line[0] += 1
    ns_head = struct.pack("<II", 1, 0xFFFFFFFF); ns_body = struct.pack("<II", s("android"), s(ANDROID))
    elements_start = len(strings)
    element(root)
    assert len(strings) == elements_start, "every string is pooled before the elements are written"
    body = chunk(0x0100, 16, ns_head, ns_body) + body + chunk(0x0101, 16, ns_head, ns_body)

    # the string pool (UTF-16)
    offs, data = [], b""
    for x in strings:
        offs.append(len(data))
        u = x.encode("utf-16-le")
        data += struct.pack("<H", len(x)) + u + b"\x00\x00"
    while len(data) % 4: data += b"\x00"
    head = struct.pack("<IIIII", len(strings), 0, 0, 28 + 4 * len(strings), 0)
    pool = chunk(0x0001, 28, head, b"".join(struct.pack("<I", o) for o in offs) + data)
    resmap = chunk(0x0180, 8, b"", b"".join(struct.pack("<I", ids[n]) for n in attr_names))
    inner = pool + resmap + body
    return struct.pack("<HHI", 0x0003, 8, 8 + len(inner)) + inner

if __name__ == "__main__":
    out, vcode, vname, idfile = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    ids = {l.split()[0]: int(l.split()[1]) for l in open(idfile) if l.strip()}
    with open(out, "wb") as f: f.write(build(manifest(vcode, vname, ids["Theme_Material_NoActionBar_Fullscreen"]), ids))
