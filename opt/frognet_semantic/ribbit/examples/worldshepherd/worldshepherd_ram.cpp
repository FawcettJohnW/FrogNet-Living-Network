// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// worldshepherd_ram.cpp -- worldshepherd-ram: WorldShepherd's own RAM manager, one executable.
//
// WorldShepherd needs nothing from its host but the memory. A SARA's read ticket is cells that SARAs write, each its
// own, once, and read (ribbit_claim_store.py): there is no ticket logic here, so this host has no operations of its
// own, as chat-ram has none. Nothing is asked of it and it answers nothing but reads.
//
// What it adds to the platform's host is a file ([TICKETS_ARE_FILED_V1]): a ticket has to outlive this process, so a
// write that says "permanent":true is appended to the file named by WORLDSHEPHERD_RAM_FILE and synced before the
// memory publishes it (the platform's [PERMANENT_CELL_V1] hook), and at start the file is written back into the
// memory in the order it was filed. Each record is one write() on a descriptor opened O_APPEND, so records from
// several writers land whole with no lock. Without that variable the host does not start; on a file with a damaged
// line it does not start either.
//
// Two writes to one family that are in flight at the same moment can be published in the other order than they
// were filed, so a restart can bring those two back with their ids exchanged. ribbit_claim_store.py says what that
// can and cannot change (its header, "after a restart").
//
//     WORLDSHEPHERD_RAM_FILE=/path/tickets.log worldshepherd-ram [--listen HOST:PORT]     answers /worldshepherd-api
#include "ram_host.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <unistd.h>
#define WORLDSHEPHERD_VERSION "worldshepherd-ram 0.6 [NO_OPERATIONS_V1] [TICKETS_ARE_FILED_V1]"
using frogram::Json;

class WorldShepherdRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "worldshepherd"; }
    std::string vendor_version() const override { return WORLDSHEPHERD_VERSION; }
    bool own_flag_decides() const override { return true; }             // a cell stays until it is written again or removed
    bool keeps_permanent() const override { return true; }

    // [PERMANENT_CELL_V1] called by the platform for each "permanent":true write, before it is published
    void permanent(const std::string& s, const std::string& v, const std::string& i, const std::string& bag) override {
        const std::string line = "{\"s\":" + Json::quote(s) + ",\"v\":" + Json::quote(v) + ",\"i\":" + Json::quote(i) + ",\"bag\":" + Json::quote(bag) + "}\n";
        if (write(fd_, line.data(), line.size()) != ssize_t(line.size()) || fdatasync(fd_) != 0)
            throw std::runtime_error("cannot file the cell in " + file_ + ": " + strerror(errno));
    }

    void listening(const std::string& bound) override {
        const char* f = getenv("WORLDSHEPHERD_RAM_FILE");
        if (!f || !*f) { fprintf(stderr, "worldshepherd-ram: WORLDSHEPHERD_RAM_FILE is not set: the tickets need a file\n"); exit(2); }
        file_ = f;
        size_t restored = 0, lineno = 0; std::ifstream in(file_); std::string line;
        resident_operation("restore", [&](std::unique_ptr<frogram::MemoryApi> m) {
            while (std::getline(in, line)) {
                ++lineno; if (line.empty()) continue;
                Json j; try { j = Json::parse(line); } catch (const std::exception& x) {
                    fprintf(stderr, "worldshepherd-ram: %s line %zu is not a record (%s): not starting on a damaged file\n", file_.c_str(), lineno, x.what()); exit(2); }
                m->write(j["s"].s, j["v"].s, j["i"].s, j["bag"].s); ++restored;
            }
            return std::string("{}");
        });
        fd_ = open(file_.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd_ < 0) { fprintf(stderr, "worldshepherd-ram: cannot open %s for append: %s\n", file_.c_str(), strerror(errno)); exit(2); }
        fprintf(stderr, "worldshepherd-ram: %s (%s) listening on %s; [TICKETS_ARE_FILED_V1] %zu cell(s) restored from %s, in the order filed\n",
                WORLDSHEPHERD_VERSION, ribbit::platform_version(), bound.c_str(), restored, file_.c_str());
    }

private:
    std::string file_; int fd_ = -1;
};

int main(int argc, char** argv) { return WorldShepherdRam().run(argc, argv); }
