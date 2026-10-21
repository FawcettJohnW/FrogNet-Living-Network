// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mqtt_load.cpp -- one load driver for any MQTT broker, so stock Mosquitto and the Ribbit broker are loaded by the
// same code and their numbers are comparable.
//
//   mqtt-load --port P [--host 127.0.0.1] [--pub-host H --pub-port P2] [--pubs 4] [--subs 4] [--topics 4] [--wildcard 0|1] [--qos 0|1|2]
//             [--pub-port P: publishers on another broker] [--size 64] [--rate 0] [--inflight 20] [--seconds 10] [--warmup 1] [--threads 2] [--proto 4|5]
//             [--label x]
//
// Publishers p = 0..pubs-1 publish to load/<p % topics>; subscribers s subscribe to load/<s % topics> (or load/# with
// --wildcard 1, every subscriber getting every message). --rate is messages per second per publisher (0: as fast as
// the broker takes them; QoS 1/2 publishers keep --inflight unacknowledged messages in flight). Each payload carries
// its send time, so the receiver measures end-to-end latency on one clock. Connections are spread over --threads
// event loops. Only what happens after --warmup counts: messages sent, acknowledged and received in the measured
// window (a message received in the window counts whenever it was sent; at saturation that includes the backlog). One JSON line on stdout:
//   sent, acked, received, expected_received (sent x subscribers per topic), lost, msgs/s sent and received,
//   latency p50/p90/p99/p999/max in microseconds, bytes in/out, connect failures.
// [DRAIN_COUNT_V1] After the window the publishers stop (sending what they already counted, [DRAIN_FLUSH_V1]) and the subscribers keep reading until none has received
// anything for 1 s (at most 30 s). Every message sent in the window is marked in its payload, so "delivered" counts
// each of those that reached a subscriber, in the window or after it, and "lost" = expected_received - delivered:
// at QoS 1/2 a message acknowledged to its publisher and never delivered. "late" counts those that arrived after the
// window (the backlog the broker still held). QoS 0 "lost" is drops the protocol allows.
#include <arpa/inet.h>
#include <fcntl.h>
#include <signal.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace {
std::string HOST = "127.0.0.1", PUB_HOST; int PUB_PORT = 0;   // [TWO_BOXES_V1] publishers on another broker (another box, another machine) than the subscribers
int PORT = 1883, PUBS = 4, SUBS = 4, TOPICS = 4, QOS = 0, SIZE = 64, INFLIGHT = 20, THREADS = 2, PROTO = 4;
bool WILDCARD = false; double RATE = 0, SECONDS = 10, WARMUP = 1; std::string LABEL = "";

int64_t now_ns() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct Hist {                                   // log-linear latency histogram, microseconds
    static const int N = 64 * 16; uint64_t b[N] = {0}; uint64_t n = 0; int64_t max = 0;
    static int idx(int64_t us) { if (us < 16) return int(us < 0 ? 0 : us); int e = 63 - __builtin_clzll(uint64_t(us)); int m = int((us >> (e - 4)) & 15); return (e - 3) * 16 + m; }
    static int64_t val(int i) { if (i < 16) return i; int e = i / 16 + 3, m = i % 16; return (int64_t(16 + m)) << (e - 4); }
    void add(int64_t us) { b[idx(us)]++; n++; if (us > max) max = us; }
    void merge(const Hist& o) { for (int i = 0; i < N; i++) b[i] += o.b[i]; n += o.n; if (o.max > max) max = o.max; }
    int64_t pct(double p) const { if (!n) return 0; uint64_t want = uint64_t(std::ceil(p * double(n))), c = 0; for (int i = 0; i < N; i++) { c += b[i]; if (c >= want) return val(i); } return max; }
};

struct Conn {
    int fd = -1; bool pub = false; int idx = 0; int topic = 0; bool connected = false, subscribed = false;
    std::string in, out; uint16_t next_mid = 1; int inflight = 0; uint64_t seq = 0; double next_send_ns = 0;
};
struct Stats { uint64_t sent = 0, acked = 0, received = 0, delivered = 0, late = 0, bytes_in = 0, bytes_out = 0, connect_fail = 0, out_of_window = 0; Hist lat; };

std::atomic<int64_t> t_start{0}, t_measure{0}, t_end{0};
std::atomic<int> ready{0};
// [HANG_DUMP_V1] SIGUSR1 (sent by load_compare when a run passes its deadline): the main thread's phase is written by the
// handler itself; each worker that is still looping prints its connections' state on its next pass.
std::atomic<int> dump_gen{0}, phase{0};          // phase: 0 connecting, 1 waiting for readiness, 2 running, 3 joined
void on_usr1(int) {
    static const char* names[] = {"connecting", "waiting for readiness", "running/draining", "workers joined"};
    int ph = phase.load(), r = ready.load(); char buf[160]; int n = 0;
    const char* a = "[HANG_DUMP_V1] driver main: phase="; while (*a) buf[n++] = *a++;
    const char* b = names[ph & 3]; while (*b) buf[n++] = *b++;
    const char* c = " ready="; while (*c) buf[n++] = *c++;
    char d[16]; int k = 0; do { d[k++] = char('0' + r % 10); r /= 10; } while (r && k < 15); while (k) buf[n++] = d[--k];
    buf[n++] = '\n'; ssize_t w = write(2, buf, size_t(n)); (void)w;
    dump_gen.fetch_add(1);
}

void put16(std::string& s, uint16_t v) { s += char(v >> 8); s += char(v & 0xFF); }
void putstr(std::string& s, const std::string& v) { put16(s, uint16_t(v.size())); s += v; }
void varint(std::string& s, uint32_t v) { do { uint8_t b = v % 128; v /= 128; if (v) b |= 128; s += char(b); } while (v); }
void packet(std::string& out, uint8_t hdr, const std::string& body) { out += char(hdr); varint(out, uint32_t(body.size())); out += body; }

void send_connect(Conn& c, const std::string& id) {
    std::string b; putstr(b, "MQTT"); b += char(PROTO); b += char(0x02); put16(b, 60);
    if (PROTO == 5) b += char(0);
    putstr(b, id); packet(c.out, 0x10, b);
}
void send_subscribe(Conn& c) {
    std::string b; put16(b, 1); if (PROTO == 5) b += char(0);
    putstr(b, WILDCARD ? "load/#" : "load/" + std::to_string(c.topic)); b += char(QOS); packet(c.out, 0x82, b);
}
void send_publish(Conn& c, Stats& st, bool counting) {
    std::string b; putstr(b, "load/" + std::to_string(c.topic));
    uint16_t mid = 0;
    if (QOS) { mid = c.next_mid++; if (!c.next_mid) c.next_mid = 1; put16(b, mid); c.inflight++; }
    if (PROTO == 5) b += char(0);
    int64_t t = now_ns(); char ts[16]; memcpy(ts, &t, 8); uint64_t sq = c.seq++; memcpy(ts + 8, &sq, 8);
    std::string payload(ts, 16); payload.append(size_t(SIZE - 16), 'x'); if (counting) payload[16] = 'W';   // sent in the window
    b += payload; packet(c.out, uint8_t(0x30 | (QOS << 1)), b);
    if (counting) st.sent++;
}
void ack(Conn& c, uint8_t type, uint16_t mid) { std::string b; put16(b, mid); packet(c.out, type, b); }

bool flush(Conn& c, Stats& st) {
    while (!c.out.empty()) {
        ssize_t w = ::send(c.fd, c.out.data(), c.out.size(), MSG_NOSIGNAL);
        if (w < 0) { if (errno == EAGAIN) return true; return false; }
        st.bytes_out += uint64_t(w); c.out.erase(0, size_t(w));
    }
    return true;
}

void handle(Conn& c, uint8_t hdr, const std::string& body, Stats& st, bool counting, bool draining, int64_t* last_rx) {
    uint8_t type = hdr >> 4;
    if (type == 2) { c.connected = true; if (!c.pub) send_subscribe(c); else ready++; return; }       // CONNACK
    if (type == 9) { c.subscribed = true; ready++; return; }                                          // SUBACK
    if (type == 4 || type == 7) { c.inflight--; if (counting) st.acked++; return; }                   // PUBACK / PUBCOMP
    if (type == 5) { uint16_t mid = uint16_t((uint8_t(body[0]) << 8) | uint8_t(body[1])); ack(c, 0x62, mid); return; }   // PUBREC -> PUBREL
    if (type == 6) { uint16_t mid = uint16_t((uint8_t(body[0]) << 8) | uint8_t(body[1])); ack(c, 0x70, mid); return; }   // PUBREL -> PUBCOMP
    if (type == 3) {                                                                                   // PUBLISH
        size_t p = 0; uint16_t tl = uint16_t((uint8_t(body[0]) << 8) | uint8_t(body[1])); p = 2 + tl;
        uint8_t q = (hdr >> 1) & 3; uint16_t mid = 0;
        if (q) { mid = uint16_t((uint8_t(body[p]) << 8) | uint8_t(body[p + 1])); p += 2; }
        if (PROTO == 5) { uint32_t pl = 0, mul = 1; uint8_t b; do { b = uint8_t(body[p++]); pl += (b & 127) * mul; mul *= 128; } while (b & 128); p += pl; }
        if (body.size() >= p + 16) {
            int64_t t; memcpy(&t, body.data() + p, 8);
            if (counting && !draining) { st.received++; st.lat.add((now_ns() - t) / 1000); }   // received in the window, whenever sent
            if (body.size() > p + 16 && body[p + 16] == 'W') { st.delivered++; if (draining) st.late++; }
            if (draining) *last_rx = now_ns();
        }
        if (q == 1) ack(c, 0x40, mid); else if (q == 2) ack(c, 0x50, mid);
    }
}

void worker(std::vector<Conn>* conns, Stats* st) {
    int ep = epoll_create1(0);
    for (auto& c : *conns) {
        c.fd = socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(c.pub && PUB_PORT ? PUB_PORT : PORT)); inet_pton(AF_INET, (c.pub && !PUB_HOST.empty() ? PUB_HOST : HOST).c_str(), &a.sin_addr);
        int one = 1; setsockopt(c.fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        if (connect(c.fd, reinterpret_cast<sockaddr*>(&a), sizeof a)) { st->connect_fail++; close(c.fd); c.fd = -1; continue; }
        fcntl(c.fd, F_SETFL, O_NONBLOCK);
        send_connect(c, std::string(c.pub ? "lp" : "ls") + std::to_string(c.idx) + "-" + std::to_string(getpid()));
        flush(c, *st);
        epoll_event ev{}; ev.events = EPOLLIN; ev.data.ptr = &c; epoll_ctl(ep, EPOLL_CTL_ADD, c.fd, &ev);
    }
    epoll_event evs[256]; char buf[65536];
    double interval = RATE > 0 ? 1e9 / RATE : 0;
    bool draining = false; int64_t last_rx = 0; int seen_dump = 0;
    for (;;) {
        int64_t t = now_ns();
        if (dump_gen.load() != seen_dump) {                                                  // [HANG_DUMP_V1]
            seen_dump = dump_gen.load();
            size_t n = 0, conn = 0, subd = 0, closed = 0, outb = 0, inb = 0; long infl = 0;
            for (auto& c : *conns) { n++; conn += c.connected; subd += c.subscribed; closed += c.fd < 0; outb += c.out.size(); inb += c.in.size(); infl += c.inflight; }
            fprintf(stderr, "[HANG_DUMP_V1] driver worker %p: conns=%zu connected=%zu subscribed=%zu closed=%zu inflight=%ld out_bytes=%zu in_bytes=%zu draining=%d quiet_ms=%lld since_end_ms=%lld\n",
                    static_cast<void*>(conns), n, conn, subd, closed, infl, outb, inb, draining ? 1 : 0,
                    draining ? (long long)((t - last_rx) / 1000000) : -1LL, t_end.load() ? (long long)((t - t_end.load()) / 1000000) : -1LL);
            int shown = 0;
            for (auto& c : *conns) {
                bool odd = c.fd < 0 || !c.connected || (!c.pub && !c.subscribed) || !c.out.empty() || (draining && c.inflight > 0);
                if (!odd || shown >= 20) continue;
                shown++;
                fprintf(stderr, "[HANG_DUMP_V1]   %s%d fd=%d connected=%d subscribed=%d inflight=%d out=%zu in=%zu\n", c.pub ? "pub" : "sub", c.idx, c.fd,
                        c.connected ? 1 : 0, c.subscribed ? 1 : 0, c.inflight, c.out.size(), c.in.size());
            }
            fflush(stderr);
        }
        int64_t end = t_end.load();
        if (end && t >= end && !draining) { draining = true; last_rx = t; }
        if (draining && (t - last_rx > int64_t(1e9) || t - end > int64_t(30e9))) break;
        bool counting = t_measure.load() && t >= t_measure.load();
        bool go = t_start.load() && t >= t_start.load() && !draining;
        int timeout = 1;
        for (auto& c : *conns) {
            if (c.fd < 0 || !c.pub || !c.connected) continue;
            // [DRAIN_FLUSH_V1] after the window no new publishes, but what is already counted as sent still goes out:
            // the QoS 0 buffer (up to 256 KB per publisher) used to be left unsent and counted as lost, in every broker.
            if (!go) { if (draining && !c.out.empty()) flush(c, *st); continue; }
            if (QOS == 0) {
                if (interval > 0) { if (c.next_send_ns == 0) c.next_send_ns = double(t); while (double(t) >= c.next_send_ns && c.out.size() < 1 << 20) { send_publish(c, *st, counting); c.next_send_ns += interval; } timeout = 0; }
                else { for (int k = 0; k < 64 && c.out.size() < 1 << 18; k++) send_publish(c, *st, counting); timeout = 0; }
            } else {
                while (c.inflight < INFLIGHT && (interval <= 0 || double(t) >= c.next_send_ns)) {
                    send_publish(c, *st, counting); if (interval > 0) c.next_send_ns = (c.next_send_ns ? c.next_send_ns : double(t)) + interval;
                }
            }
            flush(c, *st);
        }
        int n = epoll_wait(ep, evs, 256, timeout);
        for (int i = 0; i < n; i++) {
            Conn& c = *static_cast<Conn*>(evs[i].data.ptr);
            for (;;) {
                ssize_t r = recv(c.fd, buf, sizeof buf, 0);
                if (r <= 0) { if (r < 0 && errno == EAGAIN) break; epoll_ctl(ep, EPOLL_CTL_DEL, c.fd, nullptr); close(c.fd); c.fd = -1; break; }
                st->bytes_in += uint64_t(r); c.in.append(buf, size_t(r));
            }
            size_t p = 0;
            for (;;) {
                if (c.in.size() - p < 2) break;
                uint32_t len = 0, mul = 1; size_t q = p + 1; bool ok = false;
                while (q < c.in.size()) { uint8_t b = uint8_t(c.in[q++]); len += (b & 127) * mul; mul *= 128; if (!(b & 128)) { ok = true; break; } }
                if (!ok || c.in.size() - q < len) break;
                handle(c, uint8_t(c.in[p]), c.in.substr(q, len), *st, counting, draining, &last_rx);
                p = q + len;
            }
            c.in.erase(0, p);
            if (c.fd >= 0) flush(c, *st);
        }
    }
    for (auto& c : *conns) if (c.fd >= 0) { std::string d; d += char(0xE0); d += char(0); ssize_t w = ::send(c.fd, d.data(), d.size(), MSG_NOSIGNAL); (void)w; close(c.fd); }
    close(ep);
}
}  // namespace

int main(int argc, char** argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string k = argv[i], v = argv[i + 1];
        if (k == "--host") HOST = v; else if (k == "--pub-host") PUB_HOST = v; else if (k == "--port") PORT = atoi(v.c_str()); else if (k == "--pub-port") PUB_PORT = atoi(v.c_str()); else if (k == "--pubs") PUBS = atoi(v.c_str());
        else if (k == "--subs") SUBS = atoi(v.c_str()); else if (k == "--topics") TOPICS = atoi(v.c_str()); else if (k == "--wildcard") WILDCARD = atoi(v.c_str());
        else if (k == "--qos") QOS = atoi(v.c_str()); else if (k == "--size") SIZE = atoi(v.c_str()); else if (k == "--rate") RATE = atof(v.c_str());
        else if (k == "--inflight") INFLIGHT = atoi(v.c_str()); else if (k == "--seconds") SECONDS = atof(v.c_str()); else if (k == "--warmup") WARMUP = atof(v.c_str());
        else if (k == "--threads") THREADS = atoi(v.c_str()); else if (k == "--proto") PROTO = atoi(v.c_str()); else if (k == "--label") LABEL = v;
        else { fprintf(stderr, "mqtt-load: unknown option %s\n", k.c_str()); return 2; }
    }
    if (SIZE < 17) SIZE = 17;
    signal(SIGUSR1, on_usr1);
    std::vector<std::vector<Conn>> per(static_cast<size_t>(THREADS));
    for (int s = 0; s < SUBS; s++) { Conn c; c.pub = false; c.idx = s; c.topic = s % TOPICS; per[size_t(s % THREADS)].push_back(c); }
    for (int p = 0; p < PUBS; p++) { Conn c; c.pub = true; c.idx = p; c.topic = p % TOPICS; per[size_t((p + SUBS) % THREADS)].push_back(c); }
    std::vector<Stats> st(static_cast<size_t>(THREADS));
    std::vector<std::thread> th;
    for (int k = 0; k < THREADS; k++) th.emplace_back(worker, &per[size_t(k)], &st[size_t(k)]);
    phase.store(1);
    int64_t deadline = now_ns() + int64_t(60e9);         // thousands of connections take a while on a small machine
    while (ready.load() < PUBS + SUBS && now_ns() < deadline) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    int64_t s0 = now_ns();
    t_measure.store(s0 + int64_t(WARMUP * 1e9)); t_start.store(s0);
    t_end.store(s0 + int64_t((WARMUP + SECONDS) * 1e9));
    phase.store(2);
    for (auto& t : th) t.join();
    phase.store(3);
    Stats tot; for (auto& s : st) { tot.sent += s.sent; tot.acked += s.acked; tot.received += s.received; tot.delivered += s.delivered; tot.late += s.late; tot.bytes_in += s.bytes_in; tot.bytes_out += s.bytes_out; tot.connect_fail += s.connect_fail; tot.lat.merge(s.lat); }
    // subscribers per topic: each publisher's message reaches every subscriber on its topic (or every subscriber, wildcard)
    double per_msg = 0; if (WILDCARD) per_msg = SUBS; else { per_msg = 0; for (int p = 0; p < PUBS; p++) { int n = 0; for (int s = 0; s < SUBS; s++) if (s % TOPICS == p % TOPICS) n++; per_msg += n; } per_msg = PUBS ? per_msg / PUBS : 0; }
    printf("{\"label\":\"%s\",\"pubs\":%d,\"subs\":%d,\"topics\":%d,\"wildcard\":%d,\"qos\":%d,\"size\":%d,\"rate\":%.0f,\"threads\":%d,\"proto\":%d,\"seconds\":%.1f,"
           "\"ready\":%d,\"connect_fail\":%llu,\"sent\":%llu,\"acked\":%llu,\"received\":%llu,\"expected_received\":%.0f,"
           "\"sent_per_s\":%.0f,\"received_per_s\":%.0f,\"lat_us_p50\":%lld,\"lat_us_p90\":%lld,\"lat_us_p99\":%lld,\"lat_us_p999\":%lld,\"lat_us_max\":%lld,"
           "\"bytes_in\":%llu,\"bytes_out\":%llu,\"delivered\":%llu,\"late\":%llu,\"lost\":%.0f,\"drain_s\":%.1f}\n",
           LABEL.c_str(), PUBS, SUBS, TOPICS, WILDCARD ? 1 : 0, QOS, SIZE, RATE, THREADS, PROTO, SECONDS, ready.load(), (unsigned long long)tot.connect_fail,
           (unsigned long long)tot.sent, (unsigned long long)tot.acked, (unsigned long long)tot.received, double(tot.sent) * per_msg,
           double(tot.sent) / SECONDS, double(tot.received) / SECONDS, (long long)tot.lat.pct(0.5), (long long)tot.lat.pct(0.9), (long long)tot.lat.pct(0.99),
           (long long)tot.lat.pct(0.999), (long long)tot.lat.max, (unsigned long long)tot.bytes_in, (unsigned long long)tot.bytes_out,
           (unsigned long long)tot.delivered, (unsigned long long)tot.late, std::max(0.0, double(tot.sent) * per_msg - double(tot.delivered)),
           double(now_ns() - t_end.load()) / 1e9);
    return 0;
}
