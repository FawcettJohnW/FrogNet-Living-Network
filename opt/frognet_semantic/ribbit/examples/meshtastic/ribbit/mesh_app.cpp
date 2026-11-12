// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mesh_app.cpp -- [MT_APP_LIBRARY_V1] the app-facing layer on libmtribbit: cells decoded into plain structs, text
// built and encrypted here (stock's AES-CTR: nonce = packet id | from, the channel's PSK, 1-byte PSKs expanded to the
// default key as Channels::getKey does), held reads as callbacks. PKI (X25519) direct messages are not built here:
// a DM from an app is channel-encrypted, as stock sends one when it has no key for the destination.
#include "mesh_app.h"

#include <openssl/evp.h>
#include <openssl/rand.h>

#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "mt_ribbit.h"
#include "pb_decode.h"
#include "pb_encode.h"
#include "meshtastic/mesh.pb.h"
#include "meshtastic/portnums.pb.h"
#include "meshtastic/telemetry.pb.h"

namespace {
constexpr const char *VERSION = "mesh-app 0.1 [MT_APP_LIBRARY_V1]";
constexpr uint32_t BROADCAST = 0xffffffffu;
const uint8_t DEFAULT_PSK[16] = {0xd4, 0xf1, 0xbb, 0x3a, 0x20, 0x29, 0x07, 0x59, 0xf0, 0xbc, 0xff, 0xab, 0xcf, 0x4e, 0x69, 0x01};

struct Channel {
    std::string name;
    std::vector<uint8_t> key; // 16 or 32 bytes; empty = no encryption
    uint8_t hash;
};

uint32_t g_me = 0;
std::string g_name;
std::mutex g_mu;
std::vector<Channel> g_channels;
mta_node_cb g_node_cb = nullptr;
void *g_node_ctx = nullptr;
mta_text_cb g_text_cb = nullptr;
void *g_text_ctx = nullptr;
bool g_started = false;

uint8_t xorHash(const uint8_t *p, size_t n)
{
    uint8_t h = 0;
    for (size_t i = 0; i < n; i++)
        h ^= p[i];
    return h;
}

// Channels::getKey's expansion of a 1-byte PSK: index 0 = no encryption, n = the default key with its last byte + n-1
std::vector<uint8_t> expandKey(const uint8_t *psk, size_t len)
{
    if (!psk || len == 0) {
        std::vector<uint8_t> k(DEFAULT_PSK, DEFAULT_PSK + 16);
        return k;
    }
    if (len == 1) {
        if (psk[0] == 0) return {};
        std::vector<uint8_t> k(DEFAULT_PSK, DEFAULT_PSK + 16);
        k[15] = (uint8_t)(k[15] + psk[0] - 1);
        return k;
    }
    std::vector<uint8_t> k(psk, psk + len);
    if (len < 16) k.resize(16, 0);
    else if (len > 16 && len < 32) k.resize(32, 0);
    return k;
}

// stock's CryptoEngine::initNonce + encryptAESCtr: nonce = packetId (u64 LE) | fromNode (u32 LE) | 0; CTR, same both ways
bool aesCtr(const std::vector<uint8_t> &key, uint32_t from, uint32_t id, uint8_t *bytes, size_t n)
{
    if (key.empty()) return true;
    uint8_t nonce[16] = {0};
    uint64_t id64 = id;
    memcpy(nonce, &id64, 8);
    memcpy(nonce + 8, &from, 4);
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    const EVP_CIPHER *c = key.size() == 32 ? EVP_aes_256_ctr() : EVP_aes_128_ctr();
    int out = 0, fin = 0;
    bool ok = ctx && EVP_EncryptInit_ex(ctx, c, nullptr, key.data(), nonce) == 1 &&
              EVP_EncryptUpdate(ctx, bytes, &out, bytes, (int)n) == 1 && EVP_EncryptFinal_ex(ctx, bytes + out, &fin) == 1;
    EVP_CIPHER_CTX_free(ctx);
    return ok;
}

const Channel *channelByHash(uint8_t h)
{
    for (auto &c : g_channels)
        if (c.hash == h) return &c;
    return nullptr;
}

void snprintfId(char *out, size_t n, uint32_t num) { snprintf(out, n, "!%08x", num); }

bool decodeUser(const uint8_t *p, size_t len, mta_node_t &n)
{
    meshtastic_User u = meshtastic_User_init_zero;
    pb_istream_t s = pb_istream_from_buffer(p, len);
    if (!pb_decode(&s, &meshtastic_User_msg, &u)) return false;
    snprintf(n.id, sizeof n.id, "%s", u.id);
    snprintf(n.short_name, sizeof n.short_name, "%s", u.short_name);
    snprintf(n.long_name, sizeof n.long_name, "%s", u.long_name);
    return true;
}

void fillPosition(mta_node_t &n)
{
    uint8_t buf[256];
    size_t len = 0;
    uint32_t t = 0;
    if (!mtr_read_cell("pos", n.num, buf, sizeof buf, &len, &t)) return;
    meshtastic_Position p = meshtastic_Position_init_zero;
    pb_istream_t s = pb_istream_from_buffer(buf, len);
    if (!pb_decode(&s, &meshtastic_Position_msg, &p)) return;
    n.has_position = p.has_latitude_i || p.has_longitude_i;
    n.latitude = p.latitude_i * 1e-7;
    n.longitude = p.longitude_i * 1e-7;
    n.altitude = p.altitude;
}

void fillMetrics(mta_node_t &n)
{
    uint8_t buf[256];
    size_t len = 0;
    uint32_t t = 0;
    char topic[16];
    snprintf(topic, sizeof topic, "tel.%u", (unsigned)meshtastic_Telemetry_device_metrics_tag);
    if (!mtr_read_cell(topic, n.num, buf, sizeof buf, &len, &t)) return;
    meshtastic_Telemetry tm = meshtastic_Telemetry_init_zero;
    pb_istream_t s = pb_istream_from_buffer(buf, len);
    if (!pb_decode(&s, &meshtastic_Telemetry_msg, &tm) || tm.which_variant != meshtastic_Telemetry_device_metrics_tag) return;
    auto &d = tm.variant.device_metrics;
    n.has_metrics = 1;
    n.battery_level = d.battery_level;
    n.voltage = d.voltage;
    n.channel_utilization = d.channel_utilization;
    n.air_util_tx = d.air_util_tx;
    n.uptime_seconds = d.uptime_seconds;
}

void onUserCell(const char *, uint32_t node, const uint8_t *p, size_t len, uint32_t t, void *ctx)
{
    auto *pr = static_cast<std::pair<mta_node_cb, void *> *>(ctx);
    mta_node_t n = {};
    n.num = node;
    n.last_heard = t;
    if (!decodeUser(p, len, n)) return;
    fillPosition(n);
    fillMetrics(n);
    pr->first(&n, pr->second);
}

// a packet another node wrote into a family we hold: decrypt with the channel's key, hand the text on
void onPacket(const uint8_t *bytes, size_t len, uint64_t, const char *family, void *)
{
    meshtastic_MeshPacket mp = meshtastic_MeshPacket_init_zero;
    pb_istream_t s = pb_istream_from_buffer(bytes, len);
    if (!pb_decode(&s, &meshtastic_MeshPacket_msg, &mp)) return;
    if (mp.which_payload_variant != meshtastic_MeshPacket_encrypted_tag) return;
    if (mp.pki_encrypted) return; // a PKI DM: not for this layer
    const Channel *ch;
    {
        std::lock_guard<std::mutex> g(g_mu);
        ch = channelByHash(mp.channel);
    }
    if (!ch) return;
    std::vector<uint8_t> plain(mp.encrypted.bytes, mp.encrypted.bytes + mp.encrypted.size);
    if (!aesCtr(ch->key, mp.from, mp.id, plain.data(), plain.size())) return;
    meshtastic_Data d = meshtastic_Data_init_zero;
    pb_istream_t ds = pb_istream_from_buffer(plain.data(), plain.size());
    if (!pb_decode(&ds, &meshtastic_Data_msg, &d)) return; // not our key, or not a Data
    if (d.portnum != meshtastic_PortNum_TEXT_MESSAGE_APP) return;
    mta_text_t t = {};
    t.from = mp.from;
    t.to = mp.to;
    t.channel = mp.channel;
    t.id = mp.id;
    t.rx_time = mp.rx_time;
    size_t n = d.payload.size < sizeof t.text - 1 ? d.payload.size : sizeof t.text - 1;
    memcpy(t.text, d.payload.bytes, n);
    mta_text_cb cb;
    void *cx;
    {
        std::lock_guard<std::mutex> g(g_mu);
        cb = g_text_cb;
        cx = g_text_ctx;
    }
    if (cb) cb(&t, cx);
    (void)family;
}

void onFact(const char *topic, uint32_t node, const uint8_t *p, size_t len, int gone, uint32_t t, int live, void *)
{
    if (!live || node == g_me) return;
    mta_node_cb cb;
    void *cx;
    {
        std::lock_guard<std::mutex> g(g_mu);
        cb = g_node_cb;
        cx = g_node_ctx;
    }
    if (!cb) return;
    if (gone) {
        if (strcmp(topic, "user") != 0) return;
        mta_node_t n = {};
        n.num = node;
        n.gone = 1;
        snprintfId(n.id, sizeof n.id, node);
        cb(&n, cx);
        return;
    }
    // any cell of the node changed: hand the node over whole, from its cells now
    mta_node_t n = {};
    if (!mta_node(node, &n)) return;
    (void)p;
    (void)len;
    (void)t;
    cb(&n, cx);
}

void wake(void *) { mtr_take_facts(onFact, nullptr); } // the library's own thread: take at once
} // namespace

extern "C" const char *mta_version(void) { return VERSION; }
extern "C" uint32_t mta_my_num(void) { return g_me; }

extern "C" int mta_init(const char *where, const char *name)
{
    if (!name || !*name) {
        fprintf(stderr, "[MESH_APP] a name is needed\n");
        return -1;
    }
    g_name = name;
    // the node number from the name: FNV-1a, top bit clear, never the broadcast or a reserved low number
    uint32_t h = 2166136261u;
    for (const char *c = name; *c; c++) h = (h ^ (uint8_t)*c) * 16777619u;
    g_me = (h & 0x7fffffffu) | 0x10000000u;
    if (mtr_start(where && *where ? where : "here", g_me, onPacket, nullptr) != 0) return -1;
    // our user cell: the app as a node
    meshtastic_User u = meshtastic_User_init_zero;
    snprintfId(u.id, sizeof u.id, g_me);
    snprintf(u.long_name, sizeof u.long_name, "%s", name);
    snprintf(u.short_name, sizeof u.short_name, "%.4s", name);
    u.hw_model = meshtastic_HardwareModel_PRIVATE_HW;
    u.role = meshtastic_Config_DeviceConfig_Role_CLIENT_HIDDEN; // an app, not a relay
    uint8_t buf[meshtastic_User_size];
    pb_ostream_t s = pb_ostream_from_buffer(buf, sizeof buf);
    if (!pb_encode(&s, &meshtastic_User_msg, &u)) return -1;
    mtr_write_fact("user", g_me, buf, s.bytes_written);
    if (mtr_hold_facts(wake, nullptr) != 0) return -1;
    g_started = true;
    fprintf(stderr, "[MESH_APP] %s: %s is node !%08x on the memory (%s)\n", VERSION, name, g_me, mtr_version());
    return 0;
}

extern "C" size_t mta_nodes(mta_node_cb cb, void *ctx)
{
    std::pair<mta_node_cb, void *> pr{cb, ctx};
    return mtr_read_facts("user", onUserCell, &pr);
}

extern "C" int mta_node(uint32_t num, mta_node_t *out)
{
    uint8_t buf[256];
    size_t len = 0;
    uint32_t t = 0;
    if (!out || !mtr_read_cell("user", num, buf, sizeof buf, &len, &t)) return 0;
    *out = {};
    out->num = num;
    out->last_heard = t;
    if (!decodeUser(buf, len, *out)) return 0;
    fillPosition(*out);
    fillMetrics(*out);
    return 1;
}

extern "C" int mta_on_node(mta_node_cb cb, void *ctx)
{
    std::lock_guard<std::mutex> g(g_mu);
    g_node_cb = cb;
    g_node_ctx = ctx;
    return g_started ? 0 : -1;
}

extern "C" int mta_channel(const char *name, const uint8_t *psk, size_t psk_len)
{
    if (!name) return -1;
    Channel c;
    c.name = name;
    c.key = expandKey(psk, psk_len);
    c.hash = xorHash((const uint8_t *)name, strlen(name)) ^ xorHash(c.key.data(), c.key.size());
    {
        std::lock_guard<std::mutex> g(g_mu);
        g_channels.push_back(c);
    }
    mtr_hold_channel(c.hash);
    fprintf(stderr, "[MESH_APP] channel %s hash %u: holding its family\n", name, (unsigned)c.hash);
    return c.hash;
}

extern "C" int mta_on_text(mta_text_cb cb, void *ctx)
{
    std::lock_guard<std::mutex> g(g_mu);
    g_text_cb = cb;
    g_text_ctx = ctx;
    return g_started ? 0 : -1;
}

extern "C" int mta_send_text(int channel_hash, uint32_t to, const char *text)
{
    if (!g_started || !text) return -1;
    const Channel *ch;
    {
        std::lock_guard<std::mutex> g(g_mu);
        ch = channelByHash((uint8_t)channel_hash);
    }
    if (!ch) {
        fprintf(stderr, "[MESH_APP] send: channel hash %d is not one of ours\n", channel_hash);
        return -1;
    }
    meshtastic_Data d = meshtastic_Data_init_zero;
    d.portnum = meshtastic_PortNum_TEXT_MESSAGE_APP;
    size_t n = strlen(text);
    if (n > sizeof d.payload.bytes) n = sizeof d.payload.bytes;
    memcpy(d.payload.bytes, text, n);
    d.payload.size = n;
    uint8_t plain[meshtastic_Data_size];
    pb_ostream_t ds = pb_ostream_from_buffer(plain, sizeof plain);
    if (!pb_encode(&ds, &meshtastic_Data_msg, &d)) return -1;

    meshtastic_MeshPacket mp = meshtastic_MeshPacket_init_zero;
    mp.from = g_me;
    mp.to = to ? to : BROADCAST;
    mp.channel = ch->hash;
    RAND_bytes(reinterpret_cast<uint8_t *>(&mp.id), sizeof mp.id);
    mp.id |= 0x80000000u; // stock keeps ids out of the low range
    mp.hop_limit = 3;
    mp.hop_start = 3;
    mp.which_payload_variant = meshtastic_MeshPacket_encrypted_tag;
    memcpy(mp.encrypted.bytes, plain, ds.bytes_written);
    mp.encrypted.size = ds.bytes_written;
    if (!aesCtr(ch->key, mp.from, mp.id, mp.encrypted.bytes, mp.encrypted.size)) return -1;
    uint8_t buf[meshtastic_MeshPacket_size];
    pb_ostream_t ps = pb_ostream_from_buffer(buf, sizeof buf);
    if (!pb_encode(&ps, &meshtastic_MeshPacket_msg, &mp)) return -1;
    mtr_send(buf, ps.bytes_written, mp.to, mp.channel);
    return 0;
}
