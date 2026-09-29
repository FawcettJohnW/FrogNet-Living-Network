// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// tuples.hpp -- the default Ribbit API: tuples, as FrogNet discovery uses them.
//
// A tuple is one cell: (service, var, scope) -> {value, addr}. The service is the application's (SensorType in the
// node's api.php); the var names what the tuple is (a node's capability, a rank's gradient, a caller's presence); the
// scope says whose it is (a host, a node, a role, a session). A put replaces the tuple and stamps value.ts; get_all
// reads a service's tuples, filtered by freshness on the memory's own clock, and can be held until enough exist.
// A vendor whose region is plain tuples derives its client from TuplesClient and its host from ribbit::RamHost with
// no operations of its own: chat-ram and comms-ram are two such regions.
#pragma once
#include <string>
#include <vector>
#include "ram_client.hpp"

namespace ribbit {

struct Tuple {
    std::string var, scope, name, addr;   // name = "SD:<var>.<scope>", as discovery names it
    frogram::Json value;                  // what was put, with its ts
    double ts_env = 0, age_s = 0;         // when the memory last wrote it, on the memory's clock; its age now
    uint64_t id = 0;                      // the memory's write-order id
};

class TuplesClient : public RamClient {
public:
    ~TuplesClient() override;
    // Writes (service, var, scope) = value (a JSON object's text), stamping value.ts if absent and the writer's
    // address. own=true: this client removes the tuple when it closes (ephemeral coordination state); own=false: a
    // refresh write that outlives the writer and ages out by freshness instead.
    uint64_t put(const std::string& service, const std::string& var, const std::string& scope,
                 const std::string& value_json, bool own = true);
    // Every tuple of a service -- or of one var when var is given -- written within fresh_s seconds (0: any age).
    // With wait_s > 0, held by the memory until at least min_rows (default 1) exist or wait_s passes.
    // after >= 0: only tuples written after that write-order id -- with wait_s, "wake me when something newer exists".
    std::vector<Tuple> get_all(const std::string& service, int fresh_s = 0, double wait_s = 0, int min_rows = 0,
                               const std::string& var = "", int64_t after = -1);
    std::vector<Tuple> get(const std::string& service, const std::string& var, int fresh_s = 0, double wait_s = 0,
                           int min_rows = 0) { return get_all(service, fresh_s, wait_s, min_rows, var); }
    // Removes one tuple; true if it existed.
    bool remove_tuple(const std::string& service, const std::string& var, const std::string& scope);
    // Removes every tuple this client put with own=true.
    void cleanup_owned();
    // This machine's address as the tuples record it: the local address of the session's connection to the host.
    std::string my_ip();
    static std::string var_name(const std::string& var, const std::string& scope) { return "SD:" + var + "." + scope; }
    static std::string to_json(const std::vector<Tuple>& tuples);
private:
    struct Owned { std::string service, var, scope; };
    std::vector<Owned> owned_;
    std::string my_ip_;
};

}  // namespace ribbit
