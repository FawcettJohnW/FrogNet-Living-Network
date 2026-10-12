#!/usr/bin/env python3
# apply_keepalive_5s.py -- [KEEPALIVE_5S_V1] (John, 2026-10-10: "let's try taking the keepalive to 5 seconds") replaces
# [KEEPALIVE_20S_V1] on the FNWP sockets, both ends: TCP_KEEPIDLE=2, TCP_KEEPINTVL=1, TCP_KEEPCNT=3 -> a SILENT loss of an
# idle session is dead at 2 + 3*1 = 5 s; and TCP_USER_TIMEOUT=5000 ms -> a silent loss while data is unacknowledged
# (keepalive does not probe then; retransmission would take minutes) is dead at 5 s too. Probes go only when the socket is
# idle; a clean close is still instant. Applies to a tree that already carries [KEEPALIVE_20S_V1].
#   usage: apply_keepalive_5s.py <frogram.cpp> <ram_server.cpp>
import sys
OLD_VALS = 'int ka = 1, idle = 5, intvl = 5, cnt = 3;'
NEW_VALS = 'int ka = 1, idle = 2, intvl = 1, cnt = 3, uto = 5000;'
OLD_CNT = '      setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, reinterpret_cast<const fr_optval_t*>(&cnt), sizeof cnt);\n'
NEW_CNT = OLD_CNT + ('#ifdef TCP_USER_TIMEOUT\n'
                     '      setsockopt(fd, IPPROTO_TCP, TCP_USER_TIMEOUT, reinterpret_cast<const fr_optval_t*>(&uto), sizeof uto);\n'
                     '#endif\n')
# [RECV_SAYS_WHY_V1] a socket the keepalive or TCP_USER_TIMEOUT killed reads -1/ETIMEDOUT, not 0: say that, instead of
# "the far end closed the connection" (which is what 0 means). The log line is how a silent loss is told from a close.
OLD_RECV = '        if (r <= 0) throw Unreachable("the far end closed the connection");\n'
NEW_RECV = ('        if (r == 0) throw Unreachable("the far end closed the connection");\n'
            '#ifdef _WIN32\n'
            '        if (r < 0) throw Unreachable("recv failed (WSA error " + std::to_string(WSAGetLastError()) + ")");\n'
            '#else\n'
            '        if (r < 0) { int e = errno; throw Unreachable(std::string("recv: ") + strerror(e) +\n'
            '            (e == ETIMEDOUT ? " (no answer from the far end within the 5 s keepalive / user timeout)" : "")); }   // [RECV_SAYS_WHY_V1]\n'
            '#endif\n')
fp = sys.argv[1]; fs = open(fp).read()
if '[RECV_SAYS_WHY_V1]' not in fs:
    assert fs.count(OLD_RECV) == 1, f'{fp}: recv_n line not found once'
    open(fp, 'w').write(fs.replace(OLD_RECV, NEW_RECV)); print(f'[RECV_SAYS_WHY_V1] applied to {fp}')
for path in sys.argv[1:3]:
    s = open(path).read()
    if '[KEEPALIVE_5S_V1]' in s: print(f'[KEEPALIVE_5S_V1] already in {path}'); continue
    assert s.count(OLD_VALS) == 1 and s.count(OLD_CNT) == 1 and s.count('}   // [KEEPALIVE_20S_V1]') == 1, f'{path}: [KEEPALIVE_20S_V1] block not found once'
    s = s.replace(OLD_VALS, NEW_VALS).replace(OLD_CNT, NEW_CNT).replace('}   // [KEEPALIVE_20S_V1]', '}   // [KEEPALIVE_5S_V1] (was [KEEPALIVE_20S_V1])')
    open(path, 'w').write(s); print(f'[KEEPALIVE_5S_V1] applied to {path}')
