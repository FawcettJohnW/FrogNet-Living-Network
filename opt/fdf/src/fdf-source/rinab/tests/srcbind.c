// srcbind.c -- LD_PRELOAD shim: every AF_INET connect() is bound first to the source address in SRC_IP.
// Lets several test processes on one machine arrive at the RAM host from different loopback addresses.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdlib.h>
#include <sys/socket.h>
int connect(int fd, const struct sockaddr* a, socklen_t l) {
    static int (*real)(int, const struct sockaddr*, socklen_t);
    if (!real) real = dlsym(RTLD_NEXT, "connect");
    const char* s = getenv("SRC_IP");
    if (s && a && a->sa_family == AF_INET) {
        struct sockaddr_in b = {0}; b.sin_family = AF_INET; inet_pton(AF_INET, s, &b.sin_addr);
        bind(fd, (struct sockaddr*)&b, sizeof b);
    }
    return real(fd, a, l);
}
