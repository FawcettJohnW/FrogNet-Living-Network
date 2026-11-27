// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] (John, 27 Nov 2026: "Import secret kinda sucks. Let's have the Windows app display
// a QR code for the Android to import from.") A machine that holds a Role's secret shows it as a QR code on its own
// screen; the phone's camera reads it and the phone keeps it as that machine does. Nothing is sent: the secret goes from
// a screen to a camera. It is a FrogNet QR code ([FROG_QR_V1], drawn by src/app/frogqr_qt.hpp as OpenEOC's frogqr.js
// draws its own: lily pads, eyes, the logo's frog in the middle), and what it holds is a FrogNet line, short and
// plain as the enrollment line (fdfenroll1 URL APP USER) is:
//     fdfsec1 NAME STEP SECRET        (parts apart by one space; SECRET is base64url without padding)
// NAME: "default" (the network's secret, used by every Role with no file of its own) or the Role's name (its own
// secret, with the step it is at). The line is never printed, logged or written to a tuple.
#pragma once
#include <string>
#include <vector>

namespace comms {
struct SecretQr {
    std::string text;                                    // what the code says: never shown as text
    std::string name;                                    // "default" or the Role's name
    std::string hash;                                    // 8 characters of a hash of the secret: shown beside the code
    long step = 0;
    int size = 0;                                        // modules on a side
    std::vector<bool> dark;                              // size * size, row by row
};
/** The secret this machine uses for the Role, as a QR code. Throws when the machine holds none. */
SecretQr secret_qr(const std::string& role, const std::string& secrets_dir);
}  // namespace comms
