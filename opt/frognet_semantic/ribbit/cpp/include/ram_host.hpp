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

namespace ramm { class Memory; }
namespace ribbit {

struct Reply { int status; std::string body; };
// {"ok":false,"error":msg} with an HTTP status
Reply error_reply(int status, const std::string& msg);

// [SESSION_ORDER_V1] Work for the host's pool, in order: tasks run one at a time, in the order run() was called, on
// the pool's threads; different Ordered run in parallel. Lock-free (one list, one flag); run() never waits. A vendor
// keeps one per thing whose work must stay in order .
class Ordered {
public:
    Ordered(); ~Ordered();
    Ordered(const Ordered&) = delete; Ordered& operator=(const Ordered&) = delete;
    void run(std::function<void()> f);
private:
    struct Impl; Impl* d_;
};
// [ADAPTIVE_POOL_V1] a task for the process's one pool, in parallel with every other: a thread starts when a task has
// waited past the trigger and none is free, and retires after a minute idle. Never waits.
void pool_run(std::function<void()> f);
// [DEFERRED_ANSWER_V1] A region operation that cannot answer yet takes its request's continuation (non-null only on
// the thread running a request) and answers later, from any thread; the session's writer sends it.
void* defer_answer();
void answer_later(void* continuation, const std::string& body);
// [SESSION_OF_THE_CALL_V1] the session the request this thread is running arrived on (the id session_up named; 0 when
// this thread is running none): a region that keeps state for a session's life keys it by this.
uint64_t current_session();

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
    // [INLINE_REGION_OP_V1] A region operation that never waits may run on the session's own thread, in wire order,
    // instead of on a request thread of its own: no thread is created per call. Default: none do.
    virtual bool inline_operation(const std::string& op) const { (void)op; return false; }
    // A service the network API never serves: the region's secrets. Default: a name beginning with '#'.
    virtual bool private_service(const std::string& service) const { return !service.empty() && service[0] == '#'; }
    // The service whose cells a region operation returns to its caller whole, so the caller holds its own writes at
    // once ([OWN_WRITES_HELD_V1]). Default: the vendor's id.
    virtual std::string region_service() const { return vendor_id(); }
    // [SESSION_IS_LIVENESS_V1] [SESSION_PEER_V1] A session is up (its id, unique for the life of the process, and the
    // address it arrived from) / has ended. Default: nothing to do.
    virtual void session_up(uint64_t id, const std::string& peer) { (void)id; (void)peer; }
    virtual void session_down(uint64_t id) { (void)id; }

    // ---- the platform -------------------------------------------------------------------------------------------
    // Parses --listen HOST:PORT, --version and --help; serves until SIGTERM or SIGINT. A vendor's main() calls this.
    int run(int argc, char** argv);
    // Starts serving on listen_on and returns (0, or an errno): for tests that embed a host in their own process.
    int start(const std::string& listen_on, bool quiet);
    // [NODE_IS_ITS_OWN_RAM_V1] The memory this process holds (ram_memory.hpp): a process that hosts it reads and
    // writes it directly -- no session, no frame -- while other nodes reach it at the address start() listens on.
    static ramm::Memory& memory();

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
