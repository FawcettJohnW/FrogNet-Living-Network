// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mqtt_load_lib.cpp -- the MQTT load, through libmosquitto's own API: an application, as MQTT applications are written.
// Linked against stock libmosquitto it talks to a broker; against ours ([CLIENT_ON_THE_MEMORY_V1]) only to the memory.
// One binary, two roles, run as two processes (publishers on one machine, subscribers on another):
//   mqtt-load-lib pub --pubs P --topics T --qos Q [--rate R per publisher] --start EPOCH_MS --warmup W --seconds S [--linger L]
//   mqtt-load-lib sub --subs N --topics T --qos Q --start EPOCH_MS --warmup W --seconds S
// Every payload carries its send time (CLOCK_REALTIME ns, one clock on one host) and its publisher's sequence, and is
// marked 'W' when sent inside the measured window. The publisher prints what it sent in the window; the subscriber
// prints what it received in the window (on time), what arrived after it (late), and latency percentiles. The bench
// joins them: lost = sent x subscribers per topic - delivered. At QoS 1 a stock publisher keeps --inflight unacknowledged.
#include <mosquitto.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

static int64_t now_ns() { timespec t; clock_gettime(CLOCK_REALTIME, &t); return int64_t(t.tv_sec) * 1000000000 + t.tv_nsec; }
static const char* HOST = "127.0.0.1"; static int PORT = 1883, PUBS = 4, SUBS = 4, TOPICS = 4, QOS = 1, SIZE = 64, INFLIGHT = 64;
static double RATE = 0, WARMUP = 2, SECS = 6, LINGER = 0; static int64_t START_MS = 0;

struct Hist { std::atomic<uint64_t> b[64] = {}; void add(int64_t us) { int k = 0; while (us > 1 && k < 63) { us >>= 1; ++k; } b[k]++; }
    int64_t pct(double q, uint64_t n) const { uint64_t want = uint64_t(q * double(n)), s = 0; for (int k = 0; k < 64; k++) { s += b[k]; if (s > want) return int64_t(1) << k; } return 0; } };

struct Pub { mosquitto* m; std::atomic<int> inflight{0}; uint64_t seq = 0; uint64_t sent = 0, sent_w = 0; int topic; };
static void on_publish(mosquitto*, void* o, int) { static_cast<Pub*>(o)->inflight.fetch_sub(1); }

struct SubStats { std::atomic<uint64_t> on_time{0}, late{0}, received{0}; Hist lat; };
static SubStats ST; static int64_t W_END_NS = 0;
static void on_message(mosquitto*, void*, const mosquitto_message* msg) {
    if (msg->payloadlen < 17) return;
    int64_t t; memcpy(&t, msg->payload, 8);
    const int64_t now = now_ns();
    ST.received++;
    if (static_cast<const char*>(msg->payload)[16] != 'W') return;   // sent outside the window
    if (now <= W_END_NS) { ST.on_time++; ST.lat.add((now - t) / 1000); } else ST.late++;
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: mqtt-load-lib pub|sub [options]\n"); return 2; }
    const std::string role = argv[1];
    for (int i = 2; i + 1 < argc; i += 2) {
        const std::string k = argv[i]; const char* v = argv[i + 1];
        if (k == "--host") HOST = v; else if (k == "--port") PORT = atoi(v); else if (k == "--pubs") PUBS = atoi(v); else if (k == "--subs") SUBS = atoi(v);
        else if (k == "--topics") TOPICS = atoi(v); else if (k == "--qos") QOS = atoi(v); else if (k == "--size") SIZE = atoi(v); else if (k == "--rate") RATE = atof(v);
        else if (k == "--warmup") WARMUP = atof(v); else if (k == "--seconds") SECS = atof(v); else if (k == "--start") START_MS = atoll(v); else if (k == "--inflight") INFLIGHT = atoi(v); else if (k == "--linger") LINGER = atof(v);
    }
    mosquitto_lib_init();
    const int64_t start = START_MS ? START_MS * 1000000 : now_ns() + 1000000000LL, w0 = start + int64_t(WARMUP * 1e9), w1 = w0 + int64_t(SECS * 1e9);
    W_END_NS = w1;
    if (role == "sub") {
        std::vector<mosquitto*> ms;
        for (int i = 0; i < SUBS; i++) {
            mosquitto* m = mosquitto_new(("ls" + std::to_string(i) + "-" + std::to_string(getpid())).c_str(), true, nullptr);
            mosquitto_message_callback_set(m, on_message);
            if (mosquitto_connect(m, HOST, PORT, 60) != MOSQ_ERR_SUCCESS) { fprintf(stderr, "sub %d: connect failed\n", i); return 1; }
            mosquitto_loop_start(m);
            mosquitto_subscribe(m, nullptr, ("load/" + std::to_string(i % TOPICS)).c_str(), QOS);
            ms.push_back(m);
        }
        // until the window is over and nothing has arrived for 1 s (at most 30 s more): what is late is counted too
        uint64_t last = 0; int64_t quiet_since = 0;
        for (;;) { std::this_thread::sleep_for(std::chrono::milliseconds(50)); const int64_t t = now_ns();
                   const uint64_t r = ST.received.load(); if (r != last) { last = r; quiet_since = t; }
                   if (t > w1 && (t - quiet_since > 1000000000LL || t > w1 + 30000000000LL)) break; }
        for (auto* m : ms) { mosquitto_disconnect(m); mosquitto_loop_stop(m, false); mosquitto_destroy(m); }
        const uint64_t n = ST.on_time.load();
        printf("{\"role\":\"sub\",\"subs\":%d,\"topics\":%d,\"qos\":%d,\"seconds\":%g,\"on_time\":%llu,\"late\":%llu,\"on_time_per_s\":%.0f,"
               "\"lat_us_p50\":%lld,\"lat_us_p90\":%lld,\"lat_us_p99\":%lld}\n", SUBS, TOPICS, QOS, SECS, (unsigned long long)n, (unsigned long long)ST.late.load(),
               double(n) / SECS, (long long)ST.lat.pct(0.5, n), (long long)ST.lat.pct(0.9, n), (long long)ST.lat.pct(0.99, n));
        mosquitto_lib_cleanup(); return 0;
    }
    std::vector<Pub*> ps; std::vector<std::thread> th;
    for (int i = 0; i < PUBS; i++) {
        Pub* p = new Pub; p->topic = i % TOPICS;
        p->m = mosquitto_new(("lp" + std::to_string(i) + "-" + std::to_string(getpid())).c_str(), true, p);
        mosquitto_publish_callback_set(p->m, on_publish);
        if (mosquitto_connect(p->m, HOST, PORT, 60) != MOSQ_ERR_SUCCESS) { fprintf(stderr, "pub %d: connect failed\n", i); return 1; }
        mosquitto_loop_start(p->m);
        ps.push_back(p);
    }
    for (Pub* p : ps) th.emplace_back([p, start, w0, w1] {
        const std::string topic = "load/" + std::to_string(p->topic);
        std::string payload(size_t(SIZE), 'x');
        while (now_ns() < start) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const double interval = RATE > 0 ? 1e9 / RATE : 0; double next = double(now_ns());
        for (;;) {
            const int64_t t = now_ns(); if (t >= w1) break;
            if (interval > 0) { if (double(t) < next) { std::this_thread::sleep_for(std::chrono::nanoseconds(int64_t(next - double(t)))); continue; } next += interval; }
            if (QOS > 0 && p->inflight.load() >= INFLIGHT) { std::this_thread::yield(); continue; }
            const uint64_t sq = p->seq++; memcpy(&payload[0], &t, 8); memcpy(&payload[8], &sq, 8);
            const bool w = t >= w0; payload[16] = w ? 'W' : 'x';
            p->inflight.fetch_add(1);
            if (mosquitto_publish(p->m, nullptr, topic.c_str(), SIZE, payload.data(), QOS, false) != MOSQ_ERR_SUCCESS) { p->inflight.fetch_sub(1); std::this_thread::yield(); continue; }
            p->sent++; if (w) p->sent_w++;
        }
    });
    for (auto& t : th) t.join();
    uint64_t sent = 0; for (Pub* p : ps) sent += p->sent_w;
    for (Pub* p : ps) { mosquitto_disconnect(p->m); mosquitto_loop_stop(p->m, false); mosquitto_destroy(p->m); }
    printf("{\"role\":\"pub\",\"pubs\":%d,\"topics\":%d,\"qos\":%d,\"rate\":%g,\"seconds\":%g,\"sent\":%llu,\"sent_per_s\":%.0f}\n",
           PUBS, TOPICS, QOS, RATE, SECS, (unsigned long long)sent, double(sent) / SECS);
    fflush(stdout);
    // --linger: the process stays up this long after its result (a process that holds the memory: its readers finish)
    if (LINGER > 0) std::this_thread::sleep_for(std::chrono::duration<double>(LINGER));
    mosquitto_lib_cleanup(); return 0;
}
