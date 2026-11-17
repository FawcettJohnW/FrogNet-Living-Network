#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_layout_oracle.py -- [HOMA_TRANSPORT_V1] core/homa_socket.py's structs and constants are homa.h's.

The ctypes layouts in core/homa_socket.py were typed from HomaModule's homa.h. This compiles a C program against the
header itself and compares every size, every field offset and every constant with what ctypes holds, so a layout that
has drifted from the header (a new field, a changed HOMA_MAX_BPAGES) fails here and not as a corrupt system call.

    python3 core/test_homa_layout_oracle.py /path/to/HomaModule          (needs cc; needs no Homa kernel)

It proves the layouts match the header given. It does not prove the header matches the module loaded on a node:
core/homa_socket.startup_line logs the commit these layouts are from beside the node's kernel for that.
"""
import ctypes
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core import homa_socket as H   # noqa: E402

C = r"""
#include <stdio.h>
#include <stddef.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <sys/ioctl.h>
#include "homa.h"
#define S(t) printf("sizeof." #t " %zu\n", sizeof(struct t))
#define O(t, f) printf("offset." #t "." #f " %zu\n", offsetof(struct t, f))
#define K(c) printf("const." #c " %lu\n", (unsigned long)(c))
int main(void) {
    S(homa_sendmsg_args); O(homa_sendmsg_args, id); O(homa_sendmsg_args, completion_cookie);
    O(homa_sendmsg_args, flags); O(homa_sendmsg_args, reserved);
    S(homa_recvmsg_args); O(homa_recvmsg_args, id); O(homa_recvmsg_args, completion_cookie);
    O(homa_recvmsg_args, num_bpages); O(homa_recvmsg_args, reserved); O(homa_recvmsg_args, bpage_offsets);
    S(homa_rcvbuf_args); O(homa_rcvbuf_args, start); O(homa_rcvbuf_args, length);
    S(homa_abort_args); O(homa_abort_args, id); O(homa_abort_args, error);
    S(msghdr); O(msghdr, msg_name); O(msghdr, msg_namelen); O(msghdr, msg_iov); O(msghdr, msg_iovlen);
    O(msghdr, msg_control); O(msghdr, msg_controllen); O(msghdr, msg_flags);
    S(sockaddr_in); O(sockaddr_in, sin_family); O(sockaddr_in, sin_port); O(sockaddr_in, sin_addr);
    S(iovec); O(iovec, iov_base); O(iovec, iov_len);
    K(IPPROTO_HOMA); K(HOMA_MAX_MESSAGE_LENGTH); K(HOMA_BPAGE_SIZE); K(HOMA_MAX_BPAGES);
    K(SO_HOMA_RCVBUF); K(SO_HOMA_SERVER); K(HOMAIOCABORT);
    return 0;
}
"""

PY_STRUCTS = {"homa_sendmsg_args": H.homa_sendmsg_args, "homa_recvmsg_args": H.homa_recvmsg_args,
              "homa_rcvbuf_args": H.homa_rcvbuf_args, "homa_abort_args": H.homa_abort_args,
              "msghdr": H._msghdr, "sockaddr_in": H._sockaddr_in, "iovec": H._iovec}
PY_CONSTS = {"IPPROTO_HOMA": H.IPPROTO_HOMA, "HOMA_MAX_MESSAGE_LENGTH": H.HOMA_MAX_MESSAGE_LENGTH,
             "HOMA_BPAGE_SIZE": H.HOMA_BPAGE_SIZE, "HOMA_MAX_BPAGES": H.HOMA_MAX_BPAGES,
             "SO_HOMA_RCVBUF": H.SO_HOMA_RCVBUF, "SO_HOMA_SERVER": H.SO_HOMA_SERVER, "HOMAIOCABORT": H.HOMAIOCABORT}


def main() -> int:
    if len(sys.argv) != 2 or not os.path.isfile(os.path.join(sys.argv[1], "homa.h")):
        print("usage: test_homa_layout_oracle.py /path/to/HomaModule   (the directory holding homa.h)")
        return 2
    homa_dir = os.path.abspath(sys.argv[1])
    try:
        commit = subprocess.run(["git", "-C", homa_dir, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    with tempfile.TemporaryDirectory() as td:
        src, exe = os.path.join(td, "layout.c"), os.path.join(td, "layout")
        with open(src, "w") as f:
            f.write(C)
        r = subprocess.run(["cc", "-I", homa_dir, "-o", exe, src], capture_output=True, text=True)
        if r.returncode != 0:
            print("  [FAIL] the C program did not compile against %s/homa.h:\n%s" % (homa_dir, r.stderr))
            return 1
        lines = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split("\n")
    ok, n = True, 0
    for line in lines:
        if not line.strip():
            continue
        key, val = line.split(); val = int(val); kind, _, rest = key.partition(".")
        if kind == "sizeof":
            mine = ctypes.sizeof(PY_STRUCTS[rest])
        elif kind == "offset":
            st, _, fld = rest.partition(".")
            mine = getattr(PY_STRUCTS[st], fld).offset
        else:
            mine = PY_CONSTS[rest]
        n += 1
        if mine != val:
            ok = False
            print("  [FAIL] %s: homa.h says %d, core/homa_socket.py says %d" % (key, val, mine))
    print("  [%s] %d sizes, offsets and constants compared with %s/homa.h (commit %s; homa_socket.py is typed from %s)"
          % ("PASS" if ok else "FAIL", n, homa_dir, commit[:7] or "unknown", H.HOMA_PINNED_COMMIT[:7]))
    if commit and commit != H.HOMA_PINNED_COMMIT:
        print("  [NOTE] the header checked is not the commit homa_socket.py names; if this passes, update HOMA_PINNED_COMMIT")
    print("ALL HOMA-LAYOUT ORACLE CHECKPOINTS PASS" if ok else "HOMA-LAYOUT ORACLE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
