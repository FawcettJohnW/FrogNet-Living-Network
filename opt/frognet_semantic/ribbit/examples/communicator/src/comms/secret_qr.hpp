// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] (John, 27 Nov 2026: "Import secret kinda sucks. Let's have the Windows app display
// a QR code for the Android to import from.") A machine that holds a Role's secret shows it as a QR code on its own
// screen; the phone's camera reads it and the phone keeps it as that machine does. Nothing is sent: the secret goes from
// a screen to a camera. The code holds a FrogNet line, as an invitation (fdfinv1.) and a join line (fdfjoin1.) are
// (John: "The QR code should be a FrogNet QR code"):
//     fdfsec1.<base64url, no padding, of the JSON {"v":1,"name":NAME,"step":STEP,"secret":BASE64URL}>
// name: "default" (the network's secret, used by every Role with no file of its own) or the Role's name (its own
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
