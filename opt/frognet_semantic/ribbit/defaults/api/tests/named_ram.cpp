// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// named_ram.cpp -- a RAM host for the default API's tests: any vendor id, no operations of its own.
//     named-ram VENDOR [--listen HOST:PORT]
#include "ram_host.hpp"
#include <iostream>
class NamedRam : public ribbit::RamHost {
public:
    explicit NamedRam(std::string id) : id_(std::move(id)) {}
    std::string vendor_id() const override { return id_; }
    std::string vendor_version() const override { return "test"; }
private:
    std::string id_;
};
int main(int argc, char** argv) {
    if (argc < 2) { std::cerr << "usage: named-ram VENDOR [--listen HOST:PORT]\n"; return 2; }
    NamedRam host(argv[1]);
    argv[1] = argv[0];                        // the rest of the arguments are the host's own
    return host.run(argc - 1, argv + 1);
}
