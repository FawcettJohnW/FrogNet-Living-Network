// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ram_host.hpp -- the Ribbit RAM host as a base class.
//
// A RAM host is one vendor's shared memory on the network: the memory itself (cells addressed by service, variable
// and instance; row locks and no other lock; held reads), FNW1 (the semantic wire) to its participants, and the
// vendor's region -- the operations that run in the memory itself. The platform supplies everything but the region.
// A vendor derives from RamHost, says who it is, adds its operations, and its main() is one line:
//
//     class LisperRam : public ribbit::RamHost {
//         std::string vendor_id() const override      { return "lisper"; }
//         std::string vendor_version() const override { return LISPER_VERSION; }
//         bool handles(const std::string& op) const override { return op == "lisp"; }
//         ribbit::Reply operation(const std::string& op, const std::string& body) override { ... }
//     };
//     int main(int argc, char** argv) { return LisperRam().run(argc, argv); }
//
// The result is one executable -- lisper-ram -- with the vendor's API compiled in. Its user runs it, and names an
// address and port only if the default does not suit (--listen HOST:PORT). Nothing else is configured.
#pragma once
#include <functional>
#include <memory>
#include <string>
#include "frogram.hpp"

namespace ribbit {

struct Reply { int status; std::string body; };
// {"ok":false,"error":msg} with an HTTP status
Reply error_reply(int status, const std::string& msg);

class RamHost {
public:
    virtual ~RamHost() = default;

    // ---- only the vendor can say these --------------------------------------------------------------------------
    // Who this host belongs to: the vendor whose region it serves. It names the API (/<vendor_id>-api by default),
    // the executable's log lines and --version.
    virtual std::string vendor_id() const = 0;
    // The vendor's own version; --version prints it with the platform's.
    virtual std::string vendor_version() const = 0;

    // ---- defaults a vendor may override -------------------------------------------------------------------------
    // The endpoint this host answers. A request to any other path is refused.
    virtual std::string api_path() const { return "/" + vendor_id() + "-api"; }
    // Where the host listens when the user does not say: every address, on this port.
    virtual std::string default_listen() const { return "0.0.0.0:8800"; }
    // The region's own operations: a request with op=<name> that the memory does not answer itself. They run on the
    // request's own thread, in the memory itself (see resident_operation). A host without a region answers none.
    virtual bool handles(const std::string& op) const { (void)op; return false; }
    virtual Reply operation(const std::string& op, const std::string& body);
    // A service the network API never serves: the region's secrets. Default: a name beginning with '#'.
    virtual bool private_service(const std::string& service) const { return !service.empty() && service[0] == '#'; }
    // The service whose cells a region operation returns to its caller whole, so the caller holds its own writes at
    // once ([OWN_WRITES_HELD_V1]). Default: the vendor's id.
    virtual std::string region_service() const { return vendor_id(); }

    // ---- the platform -------------------------------------------------------------------------------------------
    // Parses --listen HOST:PORT, --version and --help; serves until SIGTERM or SIGINT. A vendor's main() calls this.
    int run(int argc, char** argv);
    // Starts serving on listen_on and returns (0, or an errno): for tests that embed a host in their own process.
    int start(const std::string& listen_on, bool quiet);

protected:
    // Runs one region operation with the memory itself as its memory. `f` receives a MemoryApi whose reads are of the
    // rows they name (each under its row's shared lock) and whose writes are of the operation's own rows (each under
    // its exclusive lock) -- no session, no view, no copy -- and returns the operation's result as JSON text. The reply
    // carries that result, the id of every cell the operation wrote, and the region service's cells whole.
    Reply resident_operation(const std::string& what,
                             const std::function<std::string(std::unique_ptr<frogram::MemoryApi>)>& f);
};

// The platform's version: the memory, the wire and this host.
const char* platform_version();

}  // namespace ribbit
