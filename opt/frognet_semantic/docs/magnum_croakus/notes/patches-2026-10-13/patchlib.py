# patchlib -- edits to build_magnum.js in its own source form (ASCII, \uXXXX escapes). Usage in a patch file:
#   from patchlib import *;  sub(OLD, NEW) ... ; done()
# OLD/NEW are SOURCE text: exactly as it appears in build_magnum.js. U("unicode text") turns real characters into
# the \uXXXX escapes the source uses (em dash, curly quotes, etc.). Every sub must match exactly once (cnt= otherwise).
# Run:  python3 patches/X.py FILE   -> edits FILE in place (default: a scratch copy, then builds it).
import sys, re, os, shutil, subprocess
F = sys.argv[1] if len(sys.argv) > 1 else None
SCR = '/tmp/claude-0/verify1013/scratch-' + os.path.basename(sys.argv[0]).split('.')[0]
if F is None:
    if os.path.exists(SCR): shutil.rmtree(SCR)
    shutil.copytree('/home/claude/clean/opt/frognet_semantic/docs/magnum_croakus', SCR); F = SCR + '/build_magnum.js'; BUILD = True
else: BUILD = False
s = open(F, encoding='utf-8').read()
def U(x): return ''.join(c if ord(c) < 128 else '\\u%04x' % ord(c) for c in x)
def sub(o, n, cnt=1):
    global s
    c = s.count(o)
    if c != cnt: raise SystemExit('NO MATCH (%d, want %d): %r' % (c, cnt, o[:160]))
    s = s.replace(o, n)
def has(o): return s.count(o)
def done():
    open(F, 'w', encoding='utf-8').write(s)
    if any(ord(ch) > 127 for ch in s.replace('— second output', '')): print('WARNING: non-ASCII written; use U()')
    if BUILD:
        r = subprocess.run(['node', 'build_magnum.js'], cwd=os.path.dirname(F), capture_output=True, text=True,
                           env=dict(os.environ, NODE_PATH='/opt/npm-tools/node_modules'))
        print((r.stdout + r.stderr).strip().splitlines()[-2:] if r.returncode == 0 else ('BUILD FAILED', r.stderr[-1500:]))
    print('ok', F)
