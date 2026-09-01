#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""Idempotent installer for the UnREST game-origin hook in proxy/proxy_main.py.
Serves /game from working memory only when this node is the table host (is_local_ip);
a /game aimed elsewhere falls through to normal routing and is forwarded to that host."""
import py_compile, shutil, sys, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/frognet_semantic/proxy/proxy_main.py"

IA = "from proxy.proxy_metrics import start_flusher, bump_real, observe_peer"
IB = ("\nfrom core.game_origin import GameOrigin, looks_like_game\n"
      "GAME_ORIGIN = GameOrigin()  # one game authority per proxy; codices load lazily\n")

GA = ('        return send_error_reply(self, 400, "Missing/invalid Host header", '
      'ctx=ctx, where="missing_host_header", '
      'extras={"host_header": repr(headers.get("Host") if headers else "")})')
HB = '''

    # --- UnREST game origin: if I hold this table, answer from working memory and do
    #     NOT forward. is_local_ip gate => a /game aimed at another node falls through to
    #     normal routing and is forwarded to that table host (whose hook serves it). -----
    if is_local_ip(target_ip) and (raw_path.split("?", 1)[0] == "/game" or (body and looks_like_game(
            body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)))):
        code, _resp = GAME_ORIGIN.serve(body)
        _data = _resp.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(_data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_data)
        except BrokenPipeError:
            pass
        finally:
            self.close_connection = True
        return
    # ---------------------------------------------------------------------------'''


def main():
    s = open(PATH).read(); ch = False
    if "GAME_ORIGIN = GameOrigin()" not in s:
        assert IA in s, "import anchor not found - proxy_main.py differs from expected"
        s = s.replace(IA, IA + IB, 1); ch = True; print("[+] import + GAME_ORIGIN instance")
    else:
        print("[=] import/instance already present")
    if "GAME_ORIGIN.serve(" not in s:
        assert GA in s, "dispatch anchor not found - proxy_main.py differs from expected"
        s = s.replace(GA, GA + HB, 1); ch = True; print("[+] hook (is_local_ip gated)")
    else:
        print("[=] hook already present")
    if not ch:
        print("Nothing to do."); return
    bak = f"{PATH}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(PATH, bak); print(f"[+] backup: {bak}")
    open(PATH, "w").write(s); py_compile.compile(PATH, doraise=True)
    print(f"[OK] patched and compiles: {PATH}\nNow:  systemctl restart frognet-proxy")


if __name__ == "__main__":
    main()
