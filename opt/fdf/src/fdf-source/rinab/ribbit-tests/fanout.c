/* fanout.c -- T2, fan-out: one writer, K receivers' flows, the same SDU written to every flow. On rlite's own API
 * (rina/api.h): the same binary runs over rlite and over Mode B.
 *   fanout recv NAME K N        accept K flows; count N SDUs on each; report per flow and in total
 *   fanout send NAME K N SIZE   allocate K flows to NAME; write N SDUs of SIZE bytes to every flow
 * The first 8 bytes of each SDU carry the sender's CLOCK_MONOTONIC send time (ns), the next 4 its sequence. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "rina/api.h"
static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
struct rx { int fd, n, got, inorder; uint64_t first, last, lat_sum, lat_max; };
static void* rx_main(void* a) {
    struct rx* r = a; char buf[65536]; int expect = 0;
    while (r->got < r->n) {
        ssize_t k = read(r->fd, buf, sizeof buf); if (k <= 0) break;
        uint64_t t = now_ns(), sent; uint32_t seq; memcpy(&sent, buf, 8); memcpy(&seq, buf + 8, 4);
        if (!r->got) r->first = t; r->last = t; r->got++;
        if ((int)seq == expect) r->inorder++; expect = (int)seq + 1;
        uint64_t l = t > sent ? t - sent : 0; r->lat_sum += l; if (l > r->lat_max) r->lat_max = l;
    }
    return NULL;
}
int main(int argc, char** argv) {
    if (argc < 5) { fprintf(stderr, "usage: fanout recv NAME K N | send NAME K N SIZE\n"); return 2; }
    const char* name = argv[2]; int K = atoi(argv[3]), N = atoi(argv[4]);
    if (!strcmp(argv[1], "recv")) {
        int cfd = rina_open(); if (cfd < 0 || rina_register(cfd, NULL, name, 0) < 0) { perror("register"); return 1; }
        printf("FANOUT_RECV ready K=%d\n", K); fflush(stdout);
        struct rx* rs = calloc((size_t)K, sizeof *rs); pthread_t* th = calloc((size_t)K, sizeof *th);
        for (int i = 0; i < K; i++) { rs[i].fd = rina_flow_accept(cfd, NULL, NULL, 0); if (rs[i].fd < 0) { perror("accept"); return 1; } rs[i].n = N; pthread_create(&th[i], NULL, rx_main, &rs[i]); }
        int total = 0, inorder = 0; uint64_t first = UINT64_MAX, last = 0, lat_sum = 0, lat_max = 0;
        for (int i = 0; i < K; i++) { pthread_join(th[i], NULL); total += rs[i].got; inorder += rs[i].inorder; if (rs[i].first && rs[i].first < first) first = rs[i].first; if (rs[i].last > last) last = rs[i].last; lat_sum += rs[i].lat_sum; if (rs[i].lat_max > lat_max) lat_max = rs[i].lat_max; }
        double secs = (last > first) ? (double)(last - first) / 1e9 : 0;
        printf("FANOUT_RECV K=%d N=%d delivered=%d/%d in_order=%d elapsed_s=%.3f per_flow_sdus_per_s=%.1f total_sdus_per_s=%.1f mean_latency_ms=%.3f max_latency_ms=%.3f\n",
               K, N, total, K * N, inorder, secs, secs > 0 ? (double)N / secs : 0, secs > 0 ? (double)total / secs : 0,
               total ? (double)lat_sum / total / 1e6 : 0, (double)lat_max / 1e6);
        return total == K * N ? 0 : 1;
    }
    int size = argc > 5 ? atoi(argv[5]) : 64; if (size < 12) size = 12;
    int* fd = calloc((size_t)K, sizeof *fd);
    for (int i = 0; i < K; i++) { fd[i] = rina_flow_alloc(NULL, "fanout-writer", name, NULL, 0); if (fd[i] < 0) { perror("alloc"); return 1; } }
    char* buf = malloc((size_t)size); memset(buf, 'x', (size_t)size);
    uint64_t t0 = now_ns();
    for (uint32_t s = 0; s < (uint32_t)N; s++) {
        uint64_t t = now_ns(); memcpy(buf, &t, 8); memcpy(buf + 8, &s, 4);
        for (int i = 0; i < K; i++) if (write(fd[i], buf, (size_t)size) != size) { perror("write"); return 1; }
    }
    double secs = (double)(now_ns() - t0) / 1e9;
    printf("FANOUT_SEND K=%d N=%d size=%d writes=%d elapsed_s=%.3f writes_per_s=%.1f\n", K, N, size, K * N, secs, secs > 0 ? K * N / secs : 0);
    sleep(2);                                    /* let the flows drain before closing them */
    return 0;
}
