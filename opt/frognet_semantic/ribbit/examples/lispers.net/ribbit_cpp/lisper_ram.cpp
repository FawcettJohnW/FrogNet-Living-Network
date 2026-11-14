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
#include "ribbit_space.hpp"
#include "ribbit_pp.hpp"
#include <cstdio>
#include <memory>

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

    // [REGISTER_ASKED_V1] A Map-Register is not a call. A front writes each register it receives into its own ask
    // space (lisp.register.ask / <front> / <slot>) and is done; it holds one read on the answers written to it. The
    // region holds ONE read on every front's asks (API++ ask/answer), decides each register in the memory itself --
    // authentication, site policy, the registration and its governance, the Map-Notify -- and writes the answer into
    // its own answer space (lisp.register.answer / <front> / <slot>): where to send it, and the Map-Notify if there is
    // one. Nobody waits on anybody: the front never knows when the region reads, the region never knows who reads.
    void listening(const std::string&) override {
        space_.reset(new ribbit::Space("here", "lisper"));
        registers_.reset(new ribbit::pp::Answerer(*space_, "lisp.register",
            [this](const std::string& front, const ribbit::SpaceRow& r) { answer_register(front, r); }));
        std::fprintf(stderr, "[REGISTER_ASKED_V1] the region answers the Map-Registers its fronts write in lisp.register.ask\n");
    }
private:
    std::unique_ptr<ribbit::Space> space_;
    std::unique_ptr<ribbit::pp::Answerer> registers_;
    void answer_register(const std::string& front, const ribbit::SpaceRow& r) {
        if (r.gone) return;                                 // the front has gone, and its registers with it
        Json q; try { q = Json::parse(r.text()); } catch (const std::exception&) { return; }
        const std::string head = "{\"to\":" + Json::quote(q["source"].s) + ",\"port\":" + std::to_string((long long)q["port"].n) +
                                 ",\"t\":" + std::to_string(q["t"].n);
        std::string bag;
        ribbit::Reply rep = resident_operation("lisp wire.register_notify", [&](std::unique_ptr<frogram::MemoryApi> memory) {
            ::Engine e(std::move(memory));
            return e.call("wire.register_notify", Json::parse("{\"hex\":" + Json::quote(q["hex"].s) + ",\"source\":" + Json::quote(q["source"].s) + "}"));
        });
        try {
            Json body = Json::parse(rep.body);
            if (body["ok"].type == Json::Bool && body["ok"].b) {
                Json res = Json::parse(body["result_json"].s);
                bag = head + ",\"notify_hex\":" + Json::quote(res["notify_hex"].s) + "}";
            } else bag = head + ",\"error\":" + Json::quote(body["error"].type == Json::Str ? body["error"].s : rep.body) + "}";
        } catch (const std::exception& x) { bag = head + ",\"error\":" + Json::quote(x.what()) + "}"; }
        registers_->answer(front, r.instance, bag);
    }
};

int main(int argc, char** argv) { return LisperRam().run(argc, argv); }
