// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "comms/secret_qr.hpp"
#include "channel_frame.hpp"
#include <cctype>
#include <stdexcept>
#include "../../third_party/qrcodegen/qrcodegen.hpp"

namespace comms {
static std::string base64url(const channel_frame::Bytes& b) {
    static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string o;
    size_t i = 0;
    for (; i + 2 < b.size(); i += 3) {
        const unsigned v = (unsigned(b[i]) << 16) | (unsigned(b[i + 1]) << 8) | unsigned(b[i + 2]);
        o += A[v >> 18]; o += A[(v >> 12) & 63]; o += A[(v >> 6) & 63]; o += A[v & 63];
    }
    if (i + 1 == b.size()) { const unsigned v = unsigned(b[i]) << 16; o += A[v >> 18]; o += A[(v >> 12) & 63]; }
    else if (i + 2 == b.size()) { const unsigned v = (unsigned(b[i]) << 16) | (unsigned(b[i + 1]) << 8); o += A[v >> 18]; o += A[(v >> 12) & 63]; o += A[(v >> 6) & 63]; }
    return o;
}

SecretQr secret_qr(const std::string& role, const std::string& dir) {
    const channel_frame::Secret s = channel_frame::read_secret(role, dir);     // the one reader of a secret file
    SecretQr q;
    q.name = s.own ? role : "default";
    for (char c : q.name)                                // it goes into JSON as it is: a Role's name is plain
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-' || c == '.')) throw std::runtime_error("the Role's name '" + q.name + "' cannot be put in a QR code");
    q.step = s.step;
    q.hash = channel_frame::secret_fingerprint(s.value);
    // [FROG_QR_V1] a FrogNet line, short, as the enrollment line is (fdfenroll1 URL APP USER): plain text, parts apart
    // by a space, because a frog code reads best when it is small. It is made at error correction H: the frog sits on it.
    q.text = "fdfsec1 " + q.name + " " + std::to_string(s.step) + " " + base64url(s.value);
    const qrcodegen::QrCode code = qrcodegen::QrCode::encodeText(q.text.c_str(), qrcodegen::QrCode::Ecc::HIGH);
    q.size = code.getSize();
    q.dark.resize(size_t(q.size) * size_t(q.size));
    for (int y = 0; y < q.size; ++y) for (int x = 0; x < q.size; ++x) q.dark[size_t(y) * size_t(q.size) + size_t(x)] = code.getModule(x, y);
    return q;
}
}  // namespace comms
