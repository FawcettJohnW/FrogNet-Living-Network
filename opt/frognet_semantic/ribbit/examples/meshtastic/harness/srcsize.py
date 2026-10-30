#!/usr/bin/env python3
"""Stock source size in three parts (application / core / board support), plus generated
protobuf code reported apart. Two scopes: the whole firmware src+variants tree, and only the
sources compiled into the native meshtasticd build (from its object list). Lines = cloc code lines.
The partition is by directory and is stated in the output so it can be argued with."""
import json, os, re, subprocess, sys
FW = "/home/claude/mesh/firmware"; OBJ = "/home/claude/mesh/fwb/.pio/build/native/src"
RULES = [  # first match wins
 ("generated", r"^src/mesh/generated/"),
 ("board", r"^(variants/|src/platform/|src/detect/|src/gps/|src/motion/|src/nimble/|src/mesh/wifi/|src/mesh/eth/|src/mesh/raspihttp/|src/mesh/http/|src/input/|src/buzz/|src/power/|src/sleep|src/Pca9555|src/GpioLogic|src/SPILock|src/RF95Configuration|src/mesh/[A-Za-z0-9]*(Interface|Band|RadioLib)[A-Za-z0-9]*\.(cpp|h)$|src/mesh/HardwareRNG)"),
 ("application", r"^(src/modules/|src/mqtt/|src/graphics/|src/MessageStore|src/WaypointStore|src/WaypointUtils|src/DisplayFormatters|src/AudioThread|src/AmbientLightingThread)"),
 ("core", r"^src/"),
]
def part(p):
    for name, rx in RULES:
        if re.search(rx, p): return name
    return "other"
def cloc(files):
    if not files: return 0
    lst = "/tmp/claude-0/srcsize.lst"; os.makedirs(os.path.dirname(lst), exist_ok=True)
    open(lst, "w").write("\n".join(files))
    out = subprocess.run(["cloc", "--json", "--quiet", f"--list-file={lst}"], cwd=FW, capture_output=True, text=True).stdout
    return json.loads(out)["SUM"]["code"] if out.strip() else 0
tree = []
for top in ("src", "variants"):
    for r, _, fs in os.walk(os.path.join(FW, top)):
        for f in fs:
            if f.endswith((".c", ".cpp", ".h", ".hpp")): tree.append(os.path.relpath(os.path.join(r, f), FW))
native = set()
for r, _, fs in os.walk(OBJ):
    for f in fs:
        if f.endswith(".o"):
            s = os.path.relpath(os.path.join(r, f[:-2]), OBJ)
            if os.path.exists(os.path.join(FW, "src", s)): native.add("src/" + s)
# headers that sit beside compiled sources count with them
hdrs = {p for p in tree if p.endswith((".h", ".hpp")) and any(p.rsplit(".", 1)[0] + ext in native for ext in (".cpp", ".c"))}
scopes = {"tree": tree, "native_compiled": sorted(native | hdrs)}
res = {"partition_rules": RULES}
for sc, files in scopes.items():
    by = {}
    for f in files: by.setdefault(part(f), []).append(f)
    res[sc] = {k: {"files": len(v), "code_lines": cloc(v)} for k, v in sorted(by.items())}
json.dump(res, open(sys.argv[1], "w"), indent=1)
for sc in scopes: print(sc, {k: v["code_lines"] for k, v in res[sc].items()})
