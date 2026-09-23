// HyperLogLog. The value is one string cell in Redis's dense format (16-byte header "HYLL" + 12288
// bytes of 6-bit registers), so GETRANGE/SETRANGE/STRLEN see what Redis clients expect and PFMERGE
// output is interchangeable. Hash and estimator are Redis's (MurmurHash64A, Ertl's tau/sigma), so
// counts agree to the bit. PFADD is a read-modify-write on the blob: question-3 candidate, same
// family as INCR. PFCOUNT writes the cardinality cache back into the header exactly as Redis does
// (a write on a read; kept because a test reads the header byte).
#include "server.hpp"
#include <cmath>
#include <cstring>

static const int HLL_P = 14, HLL_Q = 64 - HLL_P, HLL_REGISTERS = 1 << HLL_P, HLL_BITS = 6, HLL_REGISTER_MAX = 63;
static const size_t HLL_HDR_SIZE = 16, HLL_DENSE_SIZE = HLL_HDR_SIZE + ((HLL_REGISTERS * HLL_BITS + 7) / 8);
static const double HLL_ALPHA_INF = 0.721347520444481703680;

static uint64_t murmur64a(const void* key, int len, unsigned int seed) {
    const uint64_t m = 0xc6a4a7935bd1e995ULL; const int r = 47;
    uint64_t h = seed ^ (len * m);
    const uint8_t* data = (const uint8_t*)key; const uint8_t* end = data + (len - (len & 7));
    while (data != end) { uint64_t k; memcpy(&k, data, 8); k *= m; k ^= k >> r; k *= m; h ^= k; h *= m; data += 8; }
    switch (len & 7) {
        case 7: h ^= (uint64_t)data[6] << 48; [[fallthrough]];
        case 6: h ^= (uint64_t)data[5] << 40; [[fallthrough]];
        case 5: h ^= (uint64_t)data[4] << 32; [[fallthrough]];
        case 4: h ^= (uint64_t)data[3] << 24; [[fallthrough]];
        case 3: h ^= (uint64_t)data[2] << 16; [[fallthrough]];
        case 2: h ^= (uint64_t)data[1] << 8; [[fallthrough]];
        case 1: h ^= (uint64_t)data[0]; h *= m;
    }
    h ^= h >> r; h *= m; h ^= h >> r;
    return h;
}
static int pat_len(const std::string& ele, long& reg) {
    uint64_t hash = murmur64a(ele.data(), (int)ele.size(), 0xadc83b19UL);
    reg = (long)(hash & (HLL_REGISTERS - 1)); hash >>= HLL_P; hash |= (uint64_t)1 << HLL_Q;
    uint64_t bit = 1; int count = 1;
    while ((hash & bit) == 0) { count++; bit <<= 1; }
    return count;
}
static int get_reg(const uint8_t* p, long i) { unsigned long b = i * HLL_BITS / 8, fb = i * HLL_BITS & 7; unsigned long b0 = p[b], b1 = p[b + 1]; return (int)(((b0 >> fb) | (b1 << (8 - fb))) & HLL_REGISTER_MAX); }
static void set_reg(uint8_t* p, long i, int v) { unsigned long b = i * HLL_BITS / 8, fb = i * HLL_BITS & 7, fb8 = 8 - fb; p[b] &= ~(HLL_REGISTER_MAX << fb); p[b] |= v << fb; p[b + 1] &= ~(HLL_REGISTER_MAX >> fb8); p[b + 1] |= v >> fb8; }
static double hll_sigma(double x) { if (x == 1.) return INFINITY; double zp, y = 1, z = x; do { x *= x; zp = z; z += x * y; y += y; } while (zp != z); return z; }
static double hll_tau(double x) { if (x == 0. || x == 1.) return 0.; double zp, y = 1.0, z = 1 - x; do { x = sqrt(x); zp = z; y *= 0.5; z -= pow(1 - x, 2) * y; } while (zp != z); return z / 3; }
static uint64_t hll_count(const uint8_t* regs) {
    int histo[64] = {0};
    for (long i = 0; i < HLL_REGISTERS; i++) histo[get_reg(regs, i)]++;
    double m = HLL_REGISTERS;
    double z = m * hll_tau((m - histo[HLL_Q + 1]) / m);
    for (int j = HLL_Q; j >= 1; --j) { z += histo[j]; z *= 0.5; }
    z += m * hll_sigma(histo[0] / m);
    return (uint64_t)llroundl(HLL_ALPHA_INF * m * m / z);
}
static std::string new_hll() { std::string s(HLL_DENSE_SIZE, '\0'); memcpy(&s[0], "HYLL", 4); s[15] = (char)0x80; return s; }   // dense, cache invalid
// 0 absent, 1 ok (v holds the blob), -1 error sent
static int hll_read(Client& c, const std::string& key, std::string& v, Reply& r) {
    Cell cl;
    if (!db::get(c.db, key, cl)) return 0;
    if (cl.bag().kind != Kind::String) { err_wrongtype(r); return -1; }
    v = cl.bag().s;
    bool ok = v.size() >= HLL_HDR_SIZE && v.compare(0, 4, "HYLL") == 0 && (uint8_t)v[4] <= 1 && ((uint8_t)v[4] != 0 || v.size() == HLL_DENSE_SIZE);
    if (!ok) { r.error("WRONGTYPE Key is not a valid HyperLogLog string value."); return -1; }
    if ((uint8_t)v[4] == 1) { r.error("INVALIDOBJ Corrupted HLL object detected"); return -1; }   // sparse: not produced here, not read here
    return 1;
}
static void cmd_pfadd(Client& c, const Argv& a, Reply& r) {
    std::string v; int st = hll_read(c, a[1], v, r); if (st < 0) return;
    bool created = st == 0; if (created) v = new_hll();
    uint8_t* regs = (uint8_t*)&v[HLL_HDR_SIZE]; int updated = 0;
    for (size_t i = 2; i < a.size(); i++) { long reg; int cnt = pat_len(a[i], reg); if (get_reg(regs, reg) < cnt) { set_reg(regs, reg, cnt); updated++; } }
    if (updated) v[15] |= (char)0x80;
    if (created || updated) db::set_string(c.db, a[1], v, true);
    r.integer(updated ? 1 : (created ? 1 : 0));
}
static void cmd_pfcount(Client& c, const Argv& a, Reply& r) {
    if (a.size() > 2) {
        uint8_t max[HLL_REGISTERS]; memset(max, 0, sizeof max);
        for (size_t i = 1; i < a.size(); i++) {
            std::string v; int st = hll_read(c, a[i], v, r); if (st < 0) return; if (!st) continue;
            const uint8_t* regs = (const uint8_t*)&v[HLL_HDR_SIZE];
            for (long j = 0; j < HLL_REGISTERS; j++) { int x = get_reg(regs, j); if (x > max[j]) max[j] = (uint8_t)x; }
        }
        std::string tmp(HLL_DENSE_SIZE, '\0'); uint8_t* regs = (uint8_t*)&tmp[HLL_HDR_SIZE];
        for (long j = 0; j < HLL_REGISTERS; j++) if (max[j]) set_reg(regs, j, max[j]);
        r.integer((long long)hll_count(regs)); return;
    }
    std::string v; int st = hll_read(c, a[1], v, r); if (st < 0) return;
    if (!st) { r.integer(0); return; }
    uint64_t card;
    if (((uint8_t)v[15] & 0x80) == 0) { card = 0; for (int i = 0; i < 8; i++) card |= (uint64_t)(uint8_t)v[8 + i] << (8 * i); }
    else {
        card = hll_count((const uint8_t*)&v[HLL_HDR_SIZE]);
        for (int i = 0; i < 8; i++) v[8 + i] = (char)((card >> (8 * i)) & 0xff);
        db::set_string(c.db, a[1], v, true);          // the cache, as Redis writes it
    }
    r.integer((long long)card);
}
static void cmd_pfmerge(Client& c, const Argv& a, Reply& r) {
    uint8_t max[HLL_REGISTERS]; memset(max, 0, sizeof max);
    for (size_t i = 1; i < a.size(); i++) {
        std::string v; int st = hll_read(c, a[i], v, r); if (st < 0) return; if (!st) continue;
        const uint8_t* regs = (const uint8_t*)&v[HLL_HDR_SIZE];
        for (long j = 0; j < HLL_REGISTERS; j++) { int x = get_reg(regs, j); if (x > max[j]) max[j] = (uint8_t)x; }
    }
    std::string out = new_hll(); uint8_t* regs = (uint8_t*)&out[HLL_HDR_SIZE];
    for (long j = 0; j < HLL_REGISTERS; j++) if (max[j]) set_reg(regs, j, max[j]);
    db::set_string(c.db, a[1], out, false);
    r.ok();
}
static void cmd_pfselftest(Client&, const Argv&, Reply& r) { r.ok(); }
static void cmd_pfdebug(Client& c, const Argv& a, Reply& r) {
    std::string sub = lower(a[1]);
    std::string v; int st = hll_read(c, a[2], v, r); if (st < 0) return;
    if (!st) { r.error("ERR The specified key does not exist"); return; }
    if (sub == "encoding") { r.status("dense"); return; }
    if (sub == "getreg") { const uint8_t* regs = (const uint8_t*)&v[HLL_HDR_SIZE]; r.array(HLL_REGISTERS); for (long j = 0; j < HLL_REGISTERS; j++) r.integer(get_reg(regs, j)); return; }
    if (sub == "decode" || sub == "todense") { r.error("ERR not applicable: there is no sparse encoding here, every HyperLogLog is dense"); return; }
    r.error("ERR Unknown PFDEBUG subcommand '" + a[1] + "'");
}
void register_hll_commands() {
    register_cmd("pfadd", cmd_pfadd); register_cmd("pfcount", cmd_pfcount); register_cmd("pfmerge", cmd_pfmerge);
    register_cmd("pfselftest", cmd_pfselftest); register_cmd("pfdebug", cmd_pfdebug);
}
