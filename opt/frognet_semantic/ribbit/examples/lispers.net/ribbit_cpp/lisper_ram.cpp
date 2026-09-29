// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lisper_ram.cpp -- lisper-ram: the LISP region's shared memory, one executable.
//
// The Ribbit RAM host (ribbit/cpp: the memory, FNW1, the host) with the LISP region compiled in. Every map-server
// operation -- a whole Map-Register with its authentication, site policy, registration and Map-Notify; a site; a key;
// a policy -- runs here, in the memory itself, on a resident Engine (lisp_engine.hpp): it reads the rows it names and
// writes its own, with no session, no view and no copy ([RESIDENT_REGION_V1]).
//
//     lisper-ram [--listen HOST:PORT]        answers /lisper-api
#include "ram_host.hpp"
#include "lisp_engine.hpp"
#include "version.hpp"

class LisperRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "lisper"; }
    std::string vendor_version() const override { return RIBBIT_LISP_VERSION; }
    std::string api_path() const override { return LISPER_API; }
    // the LISP region's cells are under the service "lisp"; those an operation writes go back to its caller whole
    std::string region_service() const override { return "lisp"; }
    bool handles(const std::string& op) const override { return op == "lisp"; }
    // op=lisp, body {"operation": NAME, "args": {...}}: one Engine operation, run in the memory itself
    ribbit::Reply operation(const std::string&, const std::string& body) override {
        Json in; try { in = Json::parse(body); } catch (const std::exception&) { return ribbit::error_reply(400, "body is not a JSON object"); }
        const std::string name = in["operation"].type == Json::Str ? in["operation"].s : "";
        if (name.empty()) return ribbit::error_reply(400, "missing operation");
        const Json args = in["args"];
        return resident_operation("lisp " + name, [&](std::unique_ptr<frogram::MemoryApi> memory) {
            ::Engine e(std::move(memory));
            return e.call(name, args);
        });
    }
};

int main(int argc, char** argv) { return LisperRam().run(argc, argv); }
