#!/usr/bin/env python3
"""apply_http_delete.py RAM_SERVER_CPP -- [HTTP_FRONT_DELETE_V1] the HTTP front's peek took only "GET " and "POST", so
DELETE ?op=remove (fdfram.remove: fdf-term-serve, fdf-browser, fdf-identity) reached the FNW1 framer and the connection
was reset. A connection that starts "DELE" is HTTP too; api() already answers op=remove for DELETE. Idempotent."""
import sys
p = sys.argv[1]; s = open(p).read()
if '[HTTP_FRONT_DELETE_V1]' in s: sys.exit(0)
old = '(!memcmp(pk, "GET ", 4) || !memcmp(pk, "POST", 4))) { serve_http(fd); return; } }   // [HTTP_FRONT_V1]'
new = '(!memcmp(pk, "GET ", 4) || !memcmp(pk, "POST", 4) || !memcmp(pk, "DELE", 4))) { serve_http(fd); return; } }   // [HTTP_FRONT_V1][HTTP_FRONT_DELETE_V1]'
if old not in s: sys.exit("apply_http_delete: the HTTP front's peek is not where it was (%s)" % p)
open(p, "w").write(s.replace(old, new, 1))
