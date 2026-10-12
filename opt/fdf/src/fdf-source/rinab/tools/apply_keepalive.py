#!/usr/bin/env python3
# apply_keepalive.py -- [KEEPALIVE_20S_V1] (John, 2026-10-09: "20 seconds") kernel TCP keepalive on the FNWP socket, both
# ends: SO_KEEPALIVE, TCP_KEEPIDLE=5, TCP_KEEPINTVL=5, TCP_KEEPCNT=3 -> a SILENT loss is a dead session at 5 + 3*5 = 20 s.
# Probes go only when the socket is idle (KEEPIDLE is idle time; any traffic resets it): a socket carrying data never
# probes. Transport liveness, the same class as WireGuard's keepalive, not an application heartbeat. A clean close is
# still instant. Linux only (the Windows client keeps the OS defaults).
#   usage: apply_keepalive.py <frogram.cpp> <ram_server.cpp>
import sys
KA = ('{ int ka = 1, idle = 5, intvl = 5, cnt = 3; setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const fr_optval_t*>(&ka), sizeof ka);\n'
      '#ifdef TCP_KEEPIDLE\n'
      '      setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, reinterpret_cast<const fr_optval_t*>(&idle), sizeof idle);\n'
      '      setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, reinterpret_cast<const fr_optval_t*>(&intvl), sizeof intvl);\n'
      '      setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, reinterpret_cast<const fr_optval_t*>(&cnt), sizeof cnt);\n'
      '#endif\n'
      '    }   // [KEEPALIVE_20S_V1]\n')
def patch(path, anchor, after=True):
    s = open(path).read()
    if '[KEEPALIVE_20S_V1]' in s: print(f'[KEEPALIVE_20S_V1] already in {path}'); return
    i = s.find(anchor); assert i >= 0, f'{path}: anchor not found: {anchor[:60]}'
    j = s.find('\n', i) + 1
    s = s[:j] + '    ' + KA + s[j:]
    open(path, 'w').write(s); print(f'[KEEPALIVE_20S_V1] applied to {path}')
frog, srv = sys.argv[1], sys.argv[2]
patch(frog, 'int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);\n    return fd;')
patch(srv, 'void serve(int fd, std::string peer) {')
