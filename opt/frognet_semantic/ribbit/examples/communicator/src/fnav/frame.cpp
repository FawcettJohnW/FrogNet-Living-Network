// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "frame.hpp"
#include <cstring>

namespace fnav {

static void put16(Bytes& b, uint16_t v) { b.push_back(uint8_t(v >> 8)); b.push_back(uint8_t(v)); }
static void put32(Bytes& b, uint32_t v) { for (int s = 24; s >= 0; s -= 8) b.push_back(uint8_t(v >> s)); }
static uint16_t get16(const Bytes& b, size_t o) {
    if (o + 2 > b.size()) throw FrameError("short frame: u16 at " + std::to_string(o) + " of " + std::to_string(b.size()));
    return uint16_t(b[o] << 8 | b[o + 1]);
}
static uint32_t get32(const Bytes& b, size_t o) {
    if (o + 4 > b.size()) throw FrameError("short frame: u32 at " + std::to_string(o) + " of " + std::to_string(b.size()));
    return uint32_t(b[o]) << 24 | uint32_t(b[o + 1]) << 16 | uint32_t(b[o + 2]) << 8 | b[o + 3];
}

Bytes pack_typed(uint8_t kind, const std::string& src, const Bytes& payload) {
    if (src.size() > 0xFFFF) throw FrameError("source name longer than 65535 bytes");
    Bytes b;
    b.reserve(3 + src.size() + payload.size());
    b.push_back(kind);
    put16(b, uint16_t(src.size()));
    b.insert(b.end(), src.begin(), src.end());
    b.insert(b.end(), payload.begin(), payload.end());
    return b;
}

Typed unpack_typed(const Bytes& body) {
    if (body.empty()) throw FrameError("empty frame");
    Typed t;
    t.kind = body[0];
    uint16_t n = get16(body, 1);
    if (3 + size_t(n) > body.size()) throw FrameError("source length " + std::to_string(n) + " past the end of the frame");
    t.src.assign(body.begin() + 3, body.begin() + 3 + n);
    t.payload.assign(body.begin() + 3 + n, body.end());
    return t;
}

Bytes pack_video(uint32_t level, const Bytes& packet, uint8_t codec, bool key) {
    Bytes b;
    b.reserve(5 + packet.size());
    put32(b, (level & 0x7FFFFFFFu) | (key ? KEYFRAME_BIT : 0u));
    b.push_back(codec);
    b.insert(b.end(), packet.begin(), packet.end());
    return b;
}

Video unpack_video(const Bytes& p) {
    if (p.size() < 5) throw FrameError("video payload shorter than its 5-byte header: " + std::to_string(p.size()));
    Video v;
    uint32_t raw = get32(p, 0);
    v.level = raw & 0x7FFFFFFFu;
    v.key = (raw & KEYFRAME_BIT) != 0;
    v.codec = p[4];
    v.packet.assign(p.begin() + 5, p.end());
    return v;
}

bool video_is_key(const Bytes& body) {
    uint16_t n = get16(body, 1);
    return (get32(body, 3 + size_t(n)) & KEYFRAME_BIT) != 0;
}

Bytes pack_seg(uint16_t frame_id, uint16_t index, uint8_t flags, const Bytes& piece) {
    Bytes b;
    b.reserve(5 + piece.size());
    put16(b, frame_id); put16(b, index); b.push_back(flags);
    b.insert(b.end(), piece.begin(), piece.end());
    return b;
}

Seg unpack_seg(const Bytes& p) {
    if (p.size() < 5) throw FrameError("segment payload shorter than its 5-byte header: " + std::to_string(p.size()));
    Seg s;
    s.frame_id = get16(p, 0);
    s.index = get16(p, 2);
    s.flags = p[4];
    s.piece.assign(p.begin() + 5, p.end());
    return s;
}

Bytes with_length(const Bytes& body) {
    Bytes b;
    b.reserve(4 + body.size());
    put32(b, uint32_t(body.size()));
    b.insert(b.end(), body.begin(), body.end());
    return b;
}

bool is_abort(const Bytes& body) {
    return body.size() >= 4 && std::memcmp(body.data() + body.size() - 4, ABORT_SENTINEL, 4) == 0;
}

Bytes abort_pad(size_t remaining) {
    Bytes p(remaining);
    for (size_t i = 0; i < remaining; ++i)               // position from the END decides the byte
        p[remaining - 1 - i] = ABORT_SENTINEL[3 - (i % 4)];
    return p;
}

}  // namespace fnav
