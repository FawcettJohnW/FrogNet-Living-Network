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
        // [CONFIG_WRITTEN_ONCE_V1] configuration is written into the memory once, by whoever administers the region:
        // each operation (site.add with the site's key, site.delete, ms.policy, ms.named_locator, ms.encryption_key)
        // is a row in the administrator's own ask space, lisp.config.ask / <administrator> / <key>; the region applies
        // it in the memory and writes what came of it into lisp.config.answer. A front reads the configuration where it
        // is: nothing is sent to a front, and a front that restarts needs nothing sent again.
        config_.reset(new ribbit::pp::Answerer(*space_, "lisp.config",
            [this](const std::string& admin, const ribbit::SpaceRow& r) { apply_config(admin, r); }));
        std::fprintf(stderr, "[REGISTER_ASKED_V1] the region answers the Map-Registers its fronts write in lisp.register.ask\n"
                             "[CONFIG_WRITTEN_ONCE_V1] the region applies the configuration written in lisp.config.ask\n");
    }
private:
    std::unique_ptr<ribbit::Space> space_;
    std::unique_ptr<ribbit::pp::Answerer> registers_, config_;
    void apply_config(const std::string& admin, const ribbit::SpaceRow& r) {
        if (r.gone) return;                                 // the administrator's session ended; what it configured stays
        Json q; try { q = Json::parse(r.text()); } catch (const std::exception&) { return; }
        const std::string op = q["operation"].type == Json::Str ? q["operation"].s : "";
        std::string bag;
        if (op.empty()) bag = "{\"ok\":false,\"error\":\"no operation\"}";
        else {
            ribbit::Reply rep = resident_operation("lisp " + op, [&](std::unique_ptr<frogram::MemoryApi> memory) {
                ::Engine e(std::move(memory));
                return e.call(op, q["args"]);
            });
            try {
                Json body = Json::parse(rep.body);
                if (body["ok"].type == Json::Bool && body["ok"].b) bag = "{\"ok\":true,\"operation\":" + Json::quote(op) + ",\"result\":" + body["result_json"].s + "}";
                else bag = "{\"ok\":false,\"operation\":" + Json::quote(op) + ",\"error\":" + Json::quote(body["error"].type == Json::Str ? body["error"].s : rep.body) + "}";
            } catch (const std::exception& x) { bag = "{\"ok\":false,\"operation\":" + Json::quote(op) + ",\"error\":" + Json::quote(x.what()) + "}"; }
        }
        config_->answer(admin, r.instance, bag);
    }
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
                // [NOTIFY_AFTER_ITS_ROWS_V1] the newest row the register wrote: a front sends the Map-Notify once its own
                // views hold that row, so "notified" still means "resolves" (as lispers.net)
                uint64_t after = 0;
                for (auto& w : body["written"].a) if (w.type == Json::Arr && w.a.size() == 2 && uint64_t(w.a[1].n) > after) after = uint64_t(w.a[1].n);
                bag = head + ",\"after\":" + std::to_string(after) + ",\"notify_hex\":" + Json::quote(res["notify_hex"].s) + "}";
            } else bag = head + ",\"error\":" + Json::quote(body["error"].type == Json::Str ? body["error"].s : rep.body) + "}";
        } catch (const std::exception& x) { bag = head + ",\"error\":" + Json::quote(x.what()) + "}"; }
        registers_->answer(front, r.instance, bag);
    }
};

int main(int argc, char** argv) { return LisperRam().run(argc, argv); }
