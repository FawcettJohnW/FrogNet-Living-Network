"""apply_accept_says_so.py RAM_SERVER_CPP -- [ACCEPT_SAYS_SO_V1] platform patch, applied at build (the vendored v0.63
stays untouched). The host's accept loop dropped every accept() failure without a word ('if (fd < 0) continue'), so a
host that had stopped taking connections (fds at their limit, say) looked like a network that had gone away. Now each
kind of failure is logged with its errno and the process's fd count and limit, at most once a second per errno, and a
failure to start a connection's thread is logged and the connection closed instead of ending the accept loop."""
import sys
p = sys.argv[1]; s = open(p).read()
old = ('    std::thread([srv] { for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl); if (fd < 0) continue;\n'
       '        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip); std::thread(serve, fd, std::string(ip)).detach(); } }).detach();\n')
assert s.count(old) == 1, s.count(old)
new = ('    std::thread([srv] { int last_e = 0; std::time_t last_t = 0; long failed = 0;\n'
       '      for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl);\n'
       '        if (fd < 0) { const int e = errno; ++failed; const std::time_t now = std::time(nullptr);\n'
       '            if (e != last_e || now != last_t) { last_e = e; last_t = now;           // [ACCEPT_SAYS_SO_V1]\n'
       '                long open = -1; if (DIR* d = opendir("/proc/self/fd")) { open = 0; while (readdir(d)) ++open; closedir(d); open -= 3; }\n'
       '                struct rlimit rl{}; getrlimit(RLIMIT_NOFILE, &rl);\n'
       '                logline("[ACCEPT_SAYS_SO_V1] accept failed: " + std::string(strerror(e)) + " (errno " + std::to_string(e) + "); fds open "\n'
       '                        + (open < 0 ? std::string("all (none left to count them with)") : std::to_string(open)) + " of limit " + std::to_string((long)rl.rlim_cur) + "; failures so far " + std::to_string(failed)); }\n'
       '            if (e == EMFILE || e == ENFILE || e == ENOBUFS || e == ENOMEM) std::this_thread::sleep_for(std::chrono::milliseconds(10));\n'
       '            continue; }\n'
       '        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip);\n'
       '        try { std::thread(serve, fd, std::string(ip)).detach(); }\n'
       '        catch (const std::exception& x) { logline(std::string("[ACCEPT_SAYS_SO_V1] no thread for a connection from ") + ip + ": " + x.what() + "; closed"); FR_CLOSE(fd); } } }).detach();\n')
s = s.replace(old, new)
for inc in ("#include <dirent.h>\n", "#include <sys/resource.h>\n"):
    if inc not in s: s = inc + s
open(p, "w").write(s)
print("apply_accept_says_so: patched", p)
