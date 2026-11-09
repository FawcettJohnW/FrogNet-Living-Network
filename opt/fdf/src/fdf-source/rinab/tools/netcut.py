################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netcut.py -- [NET_CUT_V1] for the tests: the network RAM as a member reaches it through the fabric, one fixed address
# whatever is behind it. Through the fabric a member always reaches "the" network RAM at the same address; a split puts
# another RAM behind that address on each side. Via(addr).to(other) does that here: every open connection is cut (as a
# member's session sees a split: its connection ends) and new ones reach the other RAM. Nothing else is emulated.
import socket, threading


class Via:
    def __init__(self, target):
        self.target = target
        self.lock = threading.Lock()
        self.open = []
        self.ls = socket.socket(); self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0)); self.ls.listen(256)
        self.addr = "127.0.0.1:%d" % self.ls.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def to(self, target):
        with self.lock:
            self.target = target
            cut, self.open = self.open, []
        for s in cut:
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            s.close()

    def _accept(self):
        while True:
            c, _ = self.ls.accept()
            with self.lock:
                h, p = self.target.rsplit(":", 1)
                try:
                    u = socket.create_connection((h, int(p)), timeout=5); u.settimeout(None)
                except OSError:
                    c.close(); continue
                self.open += [c, u]
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pump, args=(a, b), daemon=True).start()

    @staticmethod
    def _pump(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d: break
                b.sendall(d)
        except OSError:
            pass
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass


class From:
    """[NET_CUT_V1] a device's own address, for the tests: a browser on one machine reaches the post's web server from
    `src` (a loopback address of its own), as a device on the post's Wi-Fi does from the address its lease gave it.
    The page is opened at http://127.0.0.1:<From.port>/...; the web server sees the connection come from `src`."""
    def __init__(self, src, target):
        self.src, self.target = src, target
        self.ls = socket.socket(); self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0)); self.ls.listen(256)
        self.port = self.ls.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            c, _ = self.ls.accept()
            h, p = self.target.rsplit(":", 1)
            try:
                u = socket.create_connection((h, int(p)), timeout=5, source_address=(self.src, 0)); u.settimeout(None)
            except OSError:
                c.close(); continue
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=Via._pump, args=(a, b), daemon=True).start()
