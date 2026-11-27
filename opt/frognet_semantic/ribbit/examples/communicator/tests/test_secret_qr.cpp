// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_secret_qr SECRETS_DIR ROLE OUT.pgm -- [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] draws the QR code comms-app shows for the
// Role's secret into a grey picture (8 pixels a module, 4 modules of white around it), as a camera would see it, and
// prints the secret's name, step and hash -- not the secret. The phone's own reader (frognet-android
// communicator-test, SecretQrTest) reads the picture and must end up holding the same secret.
#include "comms/secret_qr.hpp"
#include <cstdio>
#include <fstream>
#include <string>

int main(int argc, char** argv) {
    if (argc != 4) { std::fprintf(stderr, "usage: test_secret_qr SECRETS_DIR ROLE OUT.pgm\n"); return 2; }
    try {
        const comms::SecretQr q = comms::secret_qr(argv[2], argv[1]);
        const int px = 8, quiet = 4, side = (q.size + 2 * quiet) * px;
        std::string img(size_t(side) * size_t(side), char(255));
        for (int y = 0; y < q.size; ++y) for (int x = 0; x < q.size; ++x)
            if (q.dark[size_t(y) * size_t(q.size) + size_t(x)])
                for (int dy = 0; dy < px; ++dy) for (int dx = 0; dx < px; ++dx)
                    img[size_t((y + quiet) * px + dy) * size_t(side) + size_t((x + quiet) * px + dx)] = 0;
        std::ofstream f(argv[3], std::ios::binary);
        f << "P5\n" << side << " " << side << "\n255\n" << img;
        std::printf("name=%s step=%ld hash=%s modules=%d picture=%dx%d text_bytes=%zu\n", q.name.c_str(), q.step, q.hash.c_str(), q.size, side, side, q.text.size());
        return 0;
    } catch (const std::exception& e) { std::fprintf(stderr, "test_secret_qr: %s\n", e.what()); return 1; }
}
