// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// meshtastic_ram.cpp -- meshtastic-ram: the RAM host for Meshtastic on Ribbit. The memory and FNW1, answering
// /meshtastic-api, and nothing else: no region operations. Every node writes its own cells and holds reads on the
// others'; this process is the memory they share, not a server they ask.
//
//     meshtastic-ram [--listen HOST:PORT]
#include "ram_host.hpp"

class MeshtasticRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "meshtastic"; }
    std::string vendor_version() const override { return ribbit::platform_version(); }
};

int main(int argc, char** argv) { return MeshtasticRam().run(argc, argv); }
