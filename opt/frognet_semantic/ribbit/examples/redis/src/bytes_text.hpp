// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// bytes_text.hpp -- Redis bytes as the session's text. [BYTES_AS_CODE_POINTS_V1]
//
// The session carries a call's body as text (UTF-8), and Redis values are any bytes. Each byte travels as the code
// point of the same number: ASCII crosses unchanged, a byte from 0x80 up becomes two. Exact both ways, for any bytes;
// no base64, no flag, nothing to check first.
#pragma once
#include <string>

namespace rr {
inline std::string bytes_to_text(const std::string& b) {
    std::string t; t.reserve(b.size() + b.size() / 8);
    for (unsigned char c : b) { if (c < 0x80) t += char(c); else { t += char(0xC0 | (c >> 6)); t += char(0x80 | (c & 0x3F)); } }
    return t;
}
// false if the text is not what bytes_to_text writes
inline bool text_to_bytes(const std::string& t, std::string& b) {
    b.clear(); b.reserve(t.size());
    for (size_t i = 0; i < t.size(); i++) {
        unsigned char c = (unsigned char)t[i];
        if (c < 0x80) { b += char(c); continue; }
        if ((c != 0xC2 && c != 0xC3) || i + 1 >= t.size() || ((unsigned char)t[i + 1] & 0xC0) != 0x80) return false;
        b += char(((c & 0x03) << 6) | ((unsigned char)t[++i] & 0x3F));
    }
    return true;
}
}  // namespace rr
