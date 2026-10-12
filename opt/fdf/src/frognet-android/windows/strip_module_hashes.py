import struct, sys
p = sys.argv[1]; b = open(p, 'rb').read()
i = 10; n = struct.unpack('>H', b[8:10])[0]; utf = {}; k = 1
while k < n:
    t = b[i]
    if t == 1:
        L = struct.unpack('>H', b[i+1:i+3])[0]; utf[k] = b[i+3:i+3+L].decode('utf-8', 'replace'); i += 3 + L
    elif t in (3, 4): i += 5
    elif t in (5, 6): i += 9; k += 1
    elif t in (7, 8, 16, 19, 20): i += 3
    elif t in (9, 10, 11, 12, 17, 18): i += 5
    elif t == 15: i += 4
    else: raise SystemExit("cp tag %d" % t)
    k += 1
head = b[:i]; i += 6                       # access, this, super
for cnt_kind in ('interfaces',):
    c = struct.unpack('>H', b[i:i+2])[0]; i += 2 + 2 * c
for kind in ('fields', 'methods'):
    c = struct.unpack('>H', b[i:i+2])[0]; assert c == 0, kind; i += 2
mid = b[len(head):i]
c = struct.unpack('>H', b[i:i+2])[0]; i += 2; keep = []
for _ in range(c):
    nm, L = struct.unpack('>HI', b[i:i+6]); a = b[i:i+6+L]; i += 6 + L
    if utf.get(nm) == 'ModuleHashes': print('dropped ModuleHashes (%d bytes)' % L); continue
    keep.append(a)
open(p, 'wb').write(head + mid + struct.pack('>H', len(keep)) + b''.join(keep))
