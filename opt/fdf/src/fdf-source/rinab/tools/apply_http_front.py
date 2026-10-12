#!/usr/bin/env python3
"""apply_http_front.py RAM_SERVER_CPP -- [HTTP_FRONT_V1] the RAM manager also answers plain HTTP: the ram.php contract
(POST ?op=write, GET ?op=read ...) on the same port as FNW1, for clients that speak HTTP, such as the kart demo's
pure-Python Ribbit client. A connection whose first four bytes are "GET " or "POST" is one HTTP request: read, run
through the SAME api() as FNW1, answered as HTTP/1.1, closed. A read's rows are also given as "cells" (the ram.php
reply shape). FNW1 connections are untouched. Applied at build, after every other ram_server.cpp patch."""
import sys
p = sys.argv[1]; s = open(p).read()
if '[HTTP_FRONT_V1]' in s: sys.exit(0)
old = '''void serve(int fd, std::string peer) {'''
assert s.count(old) == 1, "serve() not found"
s = s.replace(old, '''// [HTTP_FRONT_V1] one plain-HTTP request on this connection (ram.php's contract), through the same api() as FNW1
void serve_http(int fd) {
    std::string req; char b[65536]; size_t he;
    while ((he = req.find("\\r\\n\\r\\n")) == std::string::npos) {
        const ssize_t n = ::recv(fd, b, sizeof b, 0); if (n <= 0 || req.size() > (1u << 20)) { FR_CLOSE(fd); return; }
        req.append(b, size_t(n)); }
    const std::string head = req.substr(0, he); std::string body = req.substr(he + 4);
    const size_t sp1 = head.find(' '), sp2 = sp1 == std::string::npos ? sp1 : head.find(' ', sp1 + 1);
    if (sp2 == std::string::npos) { FR_CLOSE(fd); return; }
    const std::string method = head.substr(0, sp1), path = head.substr(sp1 + 1, sp2 - sp1 - 1);
    std::string low = head; for (char& c : low) c = char(tolower(static_cast<unsigned char>(c)));
    size_t clen = 0; if (const size_t k = low.find("\\r\\ncontent-length:"); k != std::string::npos) clen = size_t(strtoul(low.c_str() + k + 17, nullptr, 10));
    while (body.size() < clen) { const ssize_t n = ::recv(fd, b, sizeof b, 0); if (n <= 0) { FR_CLOSE(fd); return; } body.append(b, size_t(n)); }
    Reply rr{500, "{\\"ok\\":false,\\"error\\":\\"internal\\"}"}; api(method, path, body, true, rr);
    static const std::string rows = "{\\"ok\\":true,\\"rows\\":";                      // ram.php's read reply names them cells
    if (rr.body.compare(0, rows.size(), rows) == 0) rr.body = "{\\"ok\\":true,\\"cells\\":" + rr.body.substr(rows.size());
    send_all(fd, "HTTP/1.1 " + std::to_string(rr.status) + (rr.status == 200 ? " OK" : " Error") + "\\r\\nContent-Type: application/json\\r\\nContent-Length: " +
                 std::to_string(rr.body.size()) + "\\r\\nConnection: close\\r\\n\\r\\n" + rr.body);
    FR_CLOSE(fd);
}
''' + old, 1)
old2 = '''    std::string f; if (!recv_frame(fd, f) || uint8_t(f[4]) != OP_HELLO || f.size() < 6) { FR_CLOSE(fd); return; }'''
assert s.count(old2) == 1, "the hello read not found"
s = s.replace(old2, '''    { char pk[4]; if (::recv(fd, pk, 4, MSG_PEEK | MSG_WAITALL) == 4 && (!memcmp(pk, "GET ", 4) || !memcmp(pk, "POST", 4))) { serve_http(fd); return; } }   // [HTTP_FRONT_V1]
''' + old2, 1)
open(p, 'w').write(s)
print("[HTTP_FRONT_V1] applied")
