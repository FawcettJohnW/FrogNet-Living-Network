// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogtastic_ram.cpp -- FrogTastic-RAM (frogtastic-ram): the RAM host the FrogTastic nodes share. The memory and FNW1, answering
// /frogtastic-api, and nothing else: no region operations. Every node writes its own cells and holds reads on the
// others'; this process is the memory they share, not a server they ask.
//
//     frogtastic-ram [--listen HOST:PORT]
#include "ram_host.hpp"

class FrogtasticRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "frogtastic"; }
    std::string vendor_version() const override { return ribbit::platform_version(); }
};

int main(int argc, char** argv) { return FrogtasticRam().run(argc, argv); }
