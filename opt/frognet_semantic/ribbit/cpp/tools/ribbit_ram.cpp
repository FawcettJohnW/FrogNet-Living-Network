// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_ram.cpp -- ribbit-ram: the Ribbit RAM host with no region. The memory and FNW1 alone, answering FrogNet's
// default endpoint, /ram.php: cells written, read, held and removed, and nothing else. The platform's tests run against
// it, and it is the smallest complete example of a vendor's host.
//
//     ribbit-ram [--listen HOST:PORT]
#include "ram_host.hpp"

class RibbitRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "ribbit"; }
    std::string vendor_version() const override { return ribbit::platform_version(); }
    std::string api_path() const override { return "/ram.php"; }
};

int main(int argc, char** argv) { return RibbitRam().run(argc, argv); }
