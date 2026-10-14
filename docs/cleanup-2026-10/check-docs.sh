#!/bin/bash
# check-docs.sh - T6a docs gate: the documents an operator and a developer read say only what the tree has.
#
#   bash docs/cleanup-2026-10/check-docs.sh            # from anywhere; finds the repo root from its own path
#   CHECK_DOCS_LIST=1 bash docs/cleanup-2026-10/check-docs.sh   # also list every command (b) checked
#
# Over DOCS (the documents T6a wrote or rewrote, and every AI_README.md; nothing under a history/ directory):
#   (a) every file reference (a path in backticks, a markdown link, or a bare path under etc/ opt/ usr/ var/ docs/
#       site/ or /...) names something that exists: in the tree (relative to the document's directory or any directory
#       above it, up to the repo root; a /path is looked for at the same path in the tree), or, for a path a node
#       makes at run time (/var/log/..., /opt/fdf/var/..., /etc/frognet/...), a path the tree's code names.
#   (b) every command in docs/OPERATING.md's code blocks: each program it runs exists in the tree at the path a node
#       or the checkout has it (a cd in the block is followed), or is an OS command (on PATH here, or in OS_CMDS below).
#   (c) no word of WORDS in DOCS outside an ALLOW entry (each with its reason).
#   (d) the copies are the same file: usr/local/share/frognet/{README.md,AI_READ_FIRST.md,CLAIMS.md,COPYRIGHT,LICENSE}
#       = the repo root's; opt/fdf/README.md and hub/ = opt/fdf/src/fdf-source/fdf/'s; the Windows bridge's README.txt
#       = the Android tree's windows/README.txt with CRLF line ends.
# Every problem is printed. Exit 0 = no problem; exit 1 = at least one.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 2
exec python3 -I - "$ROOT" <<'PY'
import fnmatch, glob, os, re, shutil, subprocess, sys

ROOT = sys.argv[1]
os.chdir(ROOT)

DOCS = [
    "README.md", "AI_READ_FIRST.md", "CLAIMS.md", "docs/OPERATING.md",
    "opt/frognet_semantic/README_FIRST.md", "opt/frognet_semantic/docs/ENGINEERING_POSTURE.md",
    "opt/frognet_semantic/FROGNET_NOW.md", "opt/frognet_semantic/FROGNET_NOW_guid_identity.md",
    "opt/frognet_semantic/FROGNET_PRIMER_load_me_first.md",
    "opt/frognet_semantic/simulation/SIM_STATUS.md",
    "var/www/html/README.md", "var/www/html/README.txt",
    "opt/fdf/README.md", "opt/fdf/hub/README.md",
    "opt/fdf/hub/DEPLOY-REGION-2026-10-09.md", "opt/fdf/hub/HANDOFF-REGION-2026-10-09.md",
    "opt/fdf/hub/PLAN-FROGNET-ON-FDF.md",
    "opt/fdf/src/fdf-source/docs/PLAN-BRIDGE.md", "opt/fdf/src/fdf-source/docs/PLAN-PONDS.md",
    "opt/fdf/src/fdf-source/docs/WINDOWS-BRIDGE.md",
    "opt/fdf/src/frognet-android/docs/ANDROID-HANDOFF.md",
    "opt/fdf/src/frognet-android/docs/COMMUNICATOR-ANDROID-r5.md",
    "opt/fdf/src/frognet-android/windows/README.txt",
    "opt/fdf/windows/bridge/README.txt", "opt/fdf/windows/README-windows.txt",
] + sorted(subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*AI_README.md"],
                          capture_output=True, text=True).stdout.split())
DOCS = [d for d in dict.fromkeys(DOCS) if "/history/" not in d and os.path.exists(d)]

WORDS = r"broker|passcode|GROUP_TOKEN|mediahost|165\.232\.153\.144|StreamingFrog|/home/claude|/mnt/user-data|/tmp/claude-0"
# (c) ALLOW: (document, line regex, reason)
ALLOW = [
    (r".", r"static-no-broker\.sh", "the name of the static gate script (docs/cleanup-2026-10/static-no-broker.sh)"),
    (r"^docs/OPERATING\.md$", r"old broker keys ignored", "the tunnel daemon's own log line, quoted for the operator (internet_tunnels_v3/config.py)"),
]

# (a) paths a node or a region host makes at run time: accepted when the tree's code (not a document) names them.
RUNTIME_PREFIXES = ("/var/log/", "/var/lib/", "/var/tmp/", "/run/", "/tmp/", "/etc/frognet/", "/opt/fdf/var/",
                    "/opt/fdf/hub/hub.env", "/etc/fnid", "/etc/resolv.conf", "/etc/hosts", "/etc/sudoers.d/",
                    "/etc/systemd/system/fdf-", "/etc/wireguard", "/opt/frognet_semantic/venv", "/opt/fdf/comms",
                    "/opt/fdf/BUILD", "/opt/frognet_semantic/BUILD", "/var/www/html")
# (a) ALLOW for references that are not files in the tree, each with its reason
REF_ALLOW = [   # (document regex, reference regex, reason)
    (r".", r"^build/comms-app$", "the Communicator's build output (opt/frognet_semantic/ribbit/examples/communicator: ./build.sh writes build/)"),
    (r".", r"member-cli/build/install/fdf-member-kt/bin/fdf-member-kt$", "the Kotlin member's Gradle build output (installDist), not tracked"),
    (r".", r"^build/comms-\{x86_64,arm64\}$", "make_deliverables.sh's input directories, made by tools/build_comms_static.sh"),
    (r".", r"^C:\\|^C:/", "a path on the Windows PC"),
    (r".", r"^files/fdf-[\w.-]+$", "a file in the Android app's own files/ directory on the phone"),
    (r"bridge|WINDOWS|OPERATING", r"^(bin\\amd64\\)?wintun\.dll$|^comms-app\.exe$", "the files the Windows operator adds: wintun.dll from wintun.net, comms-app.exe built from the Communicator"),
    (r".", r"^var\\?$|^var/$", "the Windows bridge's own var\\ directory, made on the PC at the first run"),
    (r".", r"^/fdf/$|^/fdf/chat\.html$|^/fln_dashboard32\.html$", "a URL path on the node's web server (Alias /fdf/ in 000-default.conf; var/www/html)"),
    (r"^(AI_READ_FIRST|CLAIMS)\.md$", r"^/?[\w.*-]+\.(html|txt)$", "a page of the website (fawcettinnovations.com), which these two files cite"),
    (r"COMMUNICATOR-ANDROID-r5", r"^ribbit/examples/communicator/android$", "the directory this note says was removed from the repository"),
    (r"ANDROID-HANDOFF", r"^frognet-android-a11\.tgz$|^MERGE-ANDROID-r16-a4\.md$", "a tarball / note of that dated build, named in its dated section (not in the tree since)"),
    (r"README\.md$", r"^/opt/communicator/build$", "fdf-services.sh's default COMMS_BIN, outside the package by design"),
    (r"OPERATING", r"site-key-myarea$", "the example area's key file (fdf-region-create writes $V/site-key-$AREA)"),
]

problems = []
def bad(kind, where, what): problems.append("%-3s %s: %s" % (kind, where, what))

files_in_tree = set(subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n"))
basenames = {os.path.basename(f) for f in files_in_tree if f}
_code_cache = None
def code_names(path):
    """True if a non-document file of the tree names this path."""
    r = subprocess.run(["git", "grep", "-l", "-I", "-E", "-e", re.escape(path) + "([^A-Za-z0-9_./-]|$)", "--", ".", ":(exclude,glob)**/*.md", ":(exclude,glob)**/*.txt",
                        ":(exclude)docs"], capture_output=True, text=True)
    return bool(r.stdout.strip())

def expand(p):
    m = re.search(r"\{([^{}]*)\}", p)
    if not m: return [p]
    return [x for alt in m.group(1).split(",") for x in expand(p[:m.start()] + alt + p[m.end():])]

KNOWN_ROOTS = ["opt/frognet_semantic", "opt/fdf", "opt/fdf/src/fdf-source", "opt/fdf/src/fdf-source/rinab",
               "opt/fdf/src/frognet-android", "docs/cleanup-2026-10", "opt/fdf/windows/bridge",
               "opt/frognet_semantic/ribbit/examples/communicator"]
def exists_from(doc, ref):
    ref = ref.replace("\\", "/")
    for r in expand(ref):
        if r.startswith("/"):
            cands = [r.lstrip("/")]
        else:
            d = os.path.dirname(doc); cands = []
            while True:
                cands.append(os.path.normpath(os.path.join(d, r)))
                if not d: break
                d = os.path.dirname(d)
            cands += [os.path.normpath(os.path.join(k, r)) for k in KNOWN_ROOTS]
        ok = False
        for c in cands:
            if any(ch in c for ch in "*?["):
                if glob.glob(c): ok = True; break
            elif os.path.lexists(c.rstrip("/")) or c.rstrip("/") == "":
                ok = True; break
        if not ok: return False
    return True

PATHISH = re.compile(r"(?<![\w.:/-])(/?(?:etc|opt|usr|var|docs|site|run)/[\w.@*{},/+-]*[\w*}/])")
EXT = re.compile(r"\.(md|sh|py|php|html|txt|json|cmd|run|tgz|conf|service|env|js|kt|kts|exe|dll|png|cnf|inc\.php|diff|log|perm|key)$")
def refs_of(text):
    out = set()
    text = re.sub(r"\$\([^)]*\)", "*", text)                                # $(uname -m) -> a glob part
    for m in re.finditer(r"\]\(([^)\s]+)\)", text):                      # markdown links
        out.add(m.group(1))
    for m in re.finditer(r"`([^`\n]+)`", text):                            # backticks: the whole span when it is a path
        if "/" in m.group(1) and os.path.lexists(m.group(1)): out.add(m.group(1)); continue
        for w in m.group(1).split():
            w = w.strip("(),;:'\"")
            if "/" in w or EXT.search(w): out.add(w)
    for m in PATHISH.finditer(re.sub(r"`[^`\n]+`", " ", text)):           # bare repo / node paths (outside backticks)
        out.add(m.group(1))
    return out

def subseq(ref):
    """A short reference (member/Leaf.kt, tun/third_party/wintun): a tracked path holding its directories in order and
    ending in its last part (a glob part matches anything)."""
    parts = [p for p in ref.replace("\\", "/").strip("/").split("/") if p]
    if len(parts) < 2: return False
    rx = re.compile("(^|/)" + "/(.*/)?".join(re.escape(p).replace("\\*", "[^/]*") for p in parts) + "(/|$)")
    return any(rx.search(f) for f in files_in_tree if f)

def skip_ref(w):
    if re.match(r"^[a-z]+://", w) or w.startswith("#") or w.startswith("mailto:"): return True
    if re.match(r"^\d+(\.\d+){0,3}/\d+$", w) or re.match(r"^\d+/(tcp|udp)$", w): return True     # CIDR, port/proto
    if any(c in w for c in "<>$=|&"): return True                           # placeholders and shell syntax
    if w in ("/", "./", "../", "...", "/.") or re.match(r"^/\d+$", w) or re.match(r"^\.[\w-]+$", w): return True
    if re.match(r"^[\w.-]+/[\w.-]+$", w) and not EXT.search(w) and w.split("/")[0] not in ("etc", "opt", "usr", "var", "docs", "site", "app", "member", "tun", "hub", "tools", "rinab", "src", "windows", "simulation", "discovery", "core", "proxy", "daemon", "internet_tunnels_v3", "frognet_route", "ribbit", "bin", "lib", "www", "dist", "deliverables", "fdf"):
        return True                                                          # words like and/or, TCP/UDP, kernel/wireguard-go
    return False

for doc in DOCS:
    text = open(doc, encoding="utf-8", errors="replace").read()
    for w in sorted(refs_of(text)):
        if skip_ref(w): continue
        w2 = w.rstrip(".")
        if exists_from(doc, w2): continue
        if "/" not in w2 and (w2 in basenames or ("*" in w2 and any(fnmatch.fnmatch(b, w2) for b in basenames))): continue   # a file named by its name alone
        if re.match(r"^[\w.-]+/$", w2) and any(("/" + w2) in ("/" + f) for f in files_in_tree): continue   # a directory named alone
        if subseq(w2): continue                                              # a tracked file inside the dirs it names
        if (w2.startswith("/") or "/" not in w2) and code_names(w2.rstrip("/")): continue   # made at run time, named by code
        if any(re.search(dr, doc) and re.search(rr, w2) for dr, rr, _ in REF_ALLOW): continue
        bad("(a)", doc, "no such file: %s" % w)

# (b) OPERATING.md commands
OS_CMDS = {"sudo", "modprobe", "apt-get", "git", "cd", "bash", "curl", "python3", "journalctl", "systemctl", "ip", "grep", "tar",
           "ufw", "nmcli", "wg", "ls", "cat", "echo"}
def blocks(md):
    lines, cur, inblk, fence = md.split("\n"), [], False, False
    for ln in lines + [""]:
        if ln.startswith("```"):
            if fence: yield cur; cur = []
            fence = not fence; continue
        if fence: cur.append(ln); continue
        if ln.startswith("    ") and not ln.lstrip().startswith(("|", "-", "*")):
            cur.append(ln[4:]); inblk = True
        elif inblk and ln.strip() == "":
            cur.append("")
        else:
            if inblk and any(x.strip() for x in cur): yield cur
            cur, inblk = [], False
    if inblk and any(x.strip() for x in cur): yield cur
ncmd = 0
for blk in blocks(open("docs/OPERATING.md").read()):
    cwd = "."                                                              # the checkout's root
    for line in blk:
        line = line.strip()
        if not line or line.startswith("#"): continue
        import shlex
        lx = shlex.shlex(line, posix=True, punctuation_chars=True); lx.whitespace_split = True
        toks, segs = list(lx), [[]]
        for t in toks:
            if t in ("&&", "||", "|", ";", "<", ">", ">>", "2>&1"):
                segs.append([]) if t in ("&&", "||", "|", ";") else segs[-1].append(None)
            else: segs[-1].append(t)
        for words in segs:
            words = [w for w in words if w is not None][:1] + [w for w in words[1:] if w is not None] if words and words[0] is not None else [w for w in words if w is not None]
            while words and (re.match(r"^[A-Z_]+=", words[0]) or words[0] in ("sudo", "env", "exec", "timeout")):
                words = words[1:] if words[0] != "timeout" else words[2:]
            if not words: continue
            prog = words[0]
            if prog in ("bash", "sh") and len(words) > 1 and not words[1].startswith("-"): prog = words[1]
            if prog == "cd" and len(words) > 1:
                t = words[1]
                cwd = "/" if t == "/" else (t if t.startswith("/") else os.path.normpath(os.path.join(cwd, t)))
                continue
            ncmd += 1
            if os.environ.get("CHECK_DOCS_LIST"): print("    (b) cwd=%-24s %s" % (cwd, " ".join(words)))
            if "/" in prog:
                if prog.startswith("/"): cand = prog.lstrip("/")
                elif cwd == "/": cand = prog
                elif cwd.startswith("/"): cand = os.path.join(cwd.lstrip("/"), prog)
                else:
                    # the checkout is the root; a clone directory name (FrogNet-Living-Network) is the root too
                    cand = os.path.normpath(os.path.join(*(p for p in cwd.split("/") if p not in (".", "FrogNet-Living-Network")), prog)) if cwd not in (".", "FrogNet-Living-Network") else prog
                if not os.path.exists(cand): bad("(b)", "docs/OPERATING.md", "%r: %s is not in the tree (looked for %s)" % (line, prog, cand))
            elif prog in OS_CMDS or shutil.which(prog):
                pass
            else:
                bad("(b)", "docs/OPERATING.md", "%r: %s is neither in the tree nor an OS command" % (line, prog))

# (c) words
nword = 0
for doc in DOCS:
    if doc.startswith("docs/cleanup-2026-10/"): continue
    for i, ln in enumerate(open(doc, encoding="utf-8", errors="replace"), 1):
        if re.search(WORDS, ln, re.I):
            nword += 1
            if any(re.search(d, doc) and re.search(rx, ln) for d, rx, _ in ALLOW): continue
            bad("(c)", "%s:%d" % (doc, i), ln.strip()[:160])

# (d) copies
def same(a, b, crlf=False):
    x = open(a, "rb").read(); y = open(b, "rb").read()
    if crlf: x = x.replace(b"\r\n", b"\n")
    return x == y
for f in ("README.md", "AI_READ_FIRST.md", "CLAIMS.md", "COPYRIGHT", "LICENSE"):
    if not same(f, "usr/local/share/frognet/" + f): bad("(d)", f, "usr/local/share/frognet/%s is not the same file" % f)
for f in ["README.md"] + ["hub/" + os.path.basename(x) for x in glob.glob("opt/fdf/hub/*.md")]:
    if not same("opt/fdf/" + f, "opt/fdf/src/fdf-source/fdf/" + f): bad("(d)", "opt/fdf/" + f, "differs from opt/fdf/src/fdf-source/fdf/" + f)
if not same("opt/fdf/windows/bridge/README.txt", "opt/fdf/src/frognet-android/windows/README.txt", crlf=True):
    bad("(d)", "opt/fdf/windows/bridge/README.txt", "differs from opt/fdf/src/frognet-android/windows/README.txt (CRLF aside)")

for p in problems: print(p)
print("check-docs: %d documents; (b) %d commands in docs/OPERATING.md; (c) %d word matches, %d allowed; %d problem(s)"
      % (len(DOCS), ncmd, nword, nword - sum(1 for p in problems if p.startswith("(c)")), len(problems)))
sys.exit(1 if problems else 0)
PY
