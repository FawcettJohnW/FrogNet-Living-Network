#!/usr/bin/env python3
# apply_connect_timeout.py -- [CONNECT_BOUNDED_V1] a client's connect to the memory is bounded at 5 s (the same bound as
# [KEEPALIVE_5S_V1] puts on a silent session). Unbounded, a connect whose SYNs go unanswered blocks for the kernel's SYN
# retries, ~2 min: found in netns-invisible-test ([RAM_IN_THE_TUNNEL_V1]) -- fdf-client started before the hub channel
# was up, its SYN left from the Internet address, and it hung 2.5 min before its restart could join through the tunnel.
# Non-blocking connect + poll(POLLOUT, 5000 ms) + SO_ERROR; the socket is made blocking again for the session.
#   usage: apply_connect_timeout.py <frogram.cpp>
import sys
OLD = '        if (::connect(fd, a->ai_addr, int(a->ai_addrlen)) == 0) break;\n'
NEW = ('#ifndef _WIN32\n'
       '        {   // [CONNECT_BOUNDED_V1] at most 5 s per address\n'
       '            int fl = fcntl(fd, F_GETFL, 0); fcntl(fd, F_SETFL, fl | O_NONBLOCK);\n'
       '            int rc = ::connect(fd, a->ai_addr, int(a->ai_addrlen));\n'
       '            if (rc != 0 && errno == EINPROGRESS) {\n'
       '                pollfd pf{}; pf.fd = fd; pf.events = POLLOUT;\n'
       '                if (::poll(&pf, 1, 5000) == 1) { int err = 0; socklen_t el = sizeof err; getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el); rc = err ? -1 : 0; }\n'
       '            }\n'
       '            fcntl(fd, F_SETFL, fl);\n'
       '            if (rc == 0) break;\n'
       '        }\n'
       '#else\n'
       + OLD +
       '#endif\n')
p = sys.argv[1]; s = open(p).read()
if '[CONNECT_BOUNDED_V1]' in s: print(f'[CONNECT_BOUNDED_V1] already in {p}'); sys.exit(0)
assert s.count(OLD) == 1, f'{p}: the connect line not found once'
s = s.replace(OLD, NEW)
if '#include <fcntl.h>' not in s:
    s = s.replace('#  include <unistd.h>\n', '#  include <unistd.h>\n#  include <fcntl.h>\n', 1)
open(p, 'w').write(s); print(f'[CONNECT_BOUNDED_V1] applied to {p}')
