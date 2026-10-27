// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_space.hpp -- [SPACE_V1] producers write, consumers read: the one library every publish/subscribe system on
// the memory is built on (MQTT, Redis, ...), each in its own tuple space (its own service names).
//
// A producer writes its truth at its own location and returns. It does not know who reads, nothing comes back to it,
// and nothing it does waits for a reader. A consumer holds reads on the families it cares about (watch); the memory
// wakes the space's one reader thread when something lands there, and the consumer is given what landed, in each
// family's write order. When and how it reads, and what it does when what it reads says something was missed (a
// writer's ring moved past what it had not read yet: the counts it carries jump), are the consumer's: nothing at the
// system level waits, acknowledges or paces anyone.
//
// Where the memory is:
//   "here"             this process holds the memory: writes, reads and watches are calls on it -- no frame, no socket
//   "here:HOST:PORT"   the same, and other nodes reach it at HOST:PORT
//   "HOST:PORT"        a memory elsewhere: one FNWP session; a write is encoded by the thread that makes it and handed
//                      to the session's sender thread, the one thread that owns the request socket; each read and each
//                      held read is its own request in flight on the session, answered through its reader thread
#pragma once
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#include "semcodec.hpp"
#include "bagarr.hpp"

namespace ribbit {

// [DECODE_ONCE_V1] arr: the bag decoded once (its values by field name: arr->get, arr->str, arr->num); bag: text, only
// for a row that came as text. text(): the bag as JSON text either way, for a reader that wants text.
struct SpaceRow { uint64_t id = 0; std::string topic, instance, bag; ribbit::BagArrP arr;
                  bool gone = false;   // [GONE_WITH_ITS_WRITER_V1] its writer's session ended: the row is gone (a held read is told)
                  std::string text() const { return arr ? arr->json() : bag; }
                  ribbit::BagArrP fields() const { return arr ? arr : ribbit::BagArr::parse(bag); } };

class Space {
public:
    // vendor: the memory's endpoint is /<vendor>-api (the host a process holds answers it too)
    Space(const std::string& where, const std::string& vendor);
    ~Space();
    Space(const Space&) = delete; Space& operator=(const Space&) = delete;
    bool local() const;
    const std::string& where() const;

    // ---- producing: a write is the whole of it ------------------------------------------------------------------
    void write(const std::string& space, const std::string& topic, const std::string& instance, const std::string& bag);
    // the bag as fields, in its shape's order (one vector per shape, kept for the Space's life)
    void write_fields(const std::string& space, const std::string& topic, const std::string& instance,
                      const std::vector<std::string>& order, semcodec::Fields fields);
    // [LINGER_BY_FAMILY_V1] what happens to this process's cells of (space, topic) when its session ends: GOES (the
    // default) at once, N > 0 seconds later, or STAYS. Said before the family's first write; topic "": every topic of the
    // space that has no choice of its own. (The memory here: this process's own writes, never gone.)
    static constexpr int32_t GOES = 0, STAYS = -1;
    void linger(const std::string& space, const std::string& topic, int32_t seconds);
    // a ring of a writer's cells on a topic: its write n is cell <writer>.<n mod ring>
    static std::string slot(const std::string& writer, uint64_t n, uint64_t ring) { return writer + "." + std::to_string(n % ring); }

    // ---- consuming ------------------------------------------------------------------------------------------------
    // What a family holds now (topic "" = the whole space; instance "" = every instance), or, with after >= 0, what
    // was written past that id -- and with wait_s > 0, a blocking read: the caller's own thread waits for it.
    std::vector<SpaceRow> read(const std::string& space, const std::string& topic, const std::string& instance = "",
                               long long after = -1, double wait_s = 0);
    // Hold a read on a family (topic "" = the whole space): from now on (after < 0) or past an id. A thread of its own
    // asks the memory for what was written past what it has, and the memory answers when something lands; each answer
    // is given to `on`, on that thread, in the family's write order, and it asks again. `on`'s first call is at once:
    // what is there past the id (rows or none), or none for a read from now on. [READERS_IN_PARALLEL_V1]
    using OnRows = std::function<void(std::vector<SpaceRow>& rows)>;
    void watch(const std::string& space, const std::string& topic, long long after, OnRows on);
    uint64_t newest(const std::string& space, const std::string& topic);   // the newest id written to a family

    // what this process has written has left it (a memory elsewhere), or the time is up
    bool flush(double timeout_s = 5.0);
    std::string stats() const;
    uint64_t refused() const;                         // writes the memory refused (a memory elsewhere): said, never retried
private:
    struct Impl; std::unique_ptr<Impl> d_;
};

}  // namespace ribbit
