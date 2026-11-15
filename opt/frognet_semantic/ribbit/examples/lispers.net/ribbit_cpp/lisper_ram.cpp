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

    // [FRONT_DECIDES_V1] A front decides each Map-Register itself and sends the rows it wrote here in batches
    // (op=lisp, operation wire.store_rows), which this region only stores ([WRITES_BACK_OFF_V1]).
    void listening(const std::string&) override {
        space_.reset(new ribbit::Space("here", "lisper"));
        // [CONFIG_WRITTEN_ONCE_V1] configuration is written into the memory once, by whoever administers the region:
        // each operation (site.add with the site's key, site.delete, ms.policy, ms.named_locator, ms.encryption_key)
        // is a row in the administrator's own ask space, lisp.config.ask / <administrator> / <key>; the region applies
        // it in the memory and writes what came of it into lisp.config.answer. A front reads the configuration where it
        // is: nothing is sent to a front, and a front that restarts needs nothing sent again.
        config_.reset(new ribbit::pp::Answerer(*space_, "lisp.config",
            [this](const std::string& admin, const ribbit::SpaceRow& r) { apply_config(admin, r); }));
        std::fprintf(stderr, "[FRONT_DECIDES_V1] fronts decide Map-Registers; their rows arrive in batches (wire.store_rows) and are stored\n"
                             "[CONFIG_WRITTEN_ONCE_V1] the region applies the configuration written in lisp.config.ask\n");
    }
private:
    std::unique_ptr<ribbit::Space> space_;
    std::unique_ptr<ribbit::pp::Answerer> config_;
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
};

int main(int argc, char** argv) { return LisperRam().run(argc, argv); }
