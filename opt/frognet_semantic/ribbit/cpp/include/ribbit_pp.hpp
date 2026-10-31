// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ribbit_pp.hpp -- API++ [API_PLUS_PLUS_V2]: convenience functions over the base API (ribbit_space.hpp: write, read,
// hold a read). Nothing here adds anything to the memory or to a host: each function is writes into the caller's own
// space and held reads of others'. Generalized from what the Redis and MQTT conversions each built by hand.
//
// Publish/subscribe here has no message server and is not built the way traditional publish/subscribe is: there is no
// subscription list, no fan-out server, no acknowledgement, no queue. A publish is a write into the publisher's own
// cells; a subscription is a read held on a family. It is offered for convenience only -- a program that thinks in
// topics can start here. Users are encouraged to explore the proper architectures: each producer writing its truth
// where it lives, and each consumer holding reads on exactly what it needs (docs/API-PLUS-PLUS.md).
//
//   publish / subscribe   a writer's ring of cells per topic; a held read that delivers in write order and says what
//                         a slow reader missed (the ring moved past it)
//   announce / capable    "I handle X": a participant's own cell in a directory; held reads find every one, now and
//                         from then on, with nothing falling between
//   offer / latest        each writer's own last value on a topic; a reader takes the newest
//   ask / answer          the asker writes its request into its own space and holds one read on its replies; the
//                         answerer holds one read on all requests and writes each reply into its own space, keyed to
//                         the asker. No call is answered: both sides only write and read.
#pragma once
#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#include "ribbit_space.hpp"

namespace ribbit::pp {

// ---- publish / subscribe ----------------------------------------------------------------------------------------
// A publish on a topic is cell (space, topic, <writer>.<n mod ring>); its bag is the caller's fields and two of
// API++'s own: "_b" the writer, "_n" its publish count on that topic (1, 2, ...).
class Publisher {
public:
    // order: the names of the fields every publish of this publisher carries, in their order
    Publisher(Space& sp, std::string space, std::string writer, std::vector<std::string> order, uint64_t ring = 16384);
    ~Publisher();
    Publisher(const Publisher&) = delete; Publisher& operator=(const Publisher&) = delete;
    // written: that is the publish done. Returns this publish's count on the topic.
    uint64_t publish(const std::string& topic, semcodec::Fields fields);
    // the same, in a shape of the caller's: `order` names the fields, in their order (the same vector each time for one
    // shape: kept by the caller for the publisher's life). Every shape counts on the topic's one count.
    uint64_t publish(const std::string& topic, const std::vector<std::string>& order, semcodec::Fields fields);
    const std::string& writer() const { return writer_; }
private:
    struct Counts; Space& sp_; std::string space_, writer_; std::vector<std::string> order_; uint64_t ring_;
    std::unique_ptr<Counts> counts_;
};

struct Delivery { uint64_t id = 0; std::string topic, writer; uint64_t n = 0; ribbit::BagArrP fields; };
using OnDelivery = std::function<void(const Delivery&)>;
// a writer's ring moved past what this read had not read yet: `gap` publishes of it on the topic were not delivered
using OnMissed = std::function<void(const std::string& topic, const std::string& writer, uint64_t gap)>;
// A read held on a topic, from now on (from < 0) or past an id. Every publish is given to `on`, on the read's own
// thread, in the topic's write order; `batch_end`, if given, after each batch the read was handed. A publisher's cells
// that went with its session are not publishes: not given.
void subscribe(Space& sp, const std::string& space, const std::string& topic, long long from, OnDelivery on,
               OnMissed missed = {}, std::function<void()> batch_end = {});

// ---- capabilities -----------------------------------------------------------------------------------------------
// (directory, "all", <capability>␟<who>): the participant's own cell saying it handles `capability`; `about` is
// whatever it wants a finder to know (a bag, JSON text).
void announce(Space& sp, const std::string& directory, const std::string& capability, const std::string& who,
              const std::string& about = "{}");
struct Capable { uint64_t id = 0; std::string capability, who; ribbit::BagArrP about; bool gone = false;
                 bool already = false; };   // already: announced before capable() was called (its first read)
// every participant whose capability `match` accepts: those announced now, at once, then each one announced from
// then on -- the directory read past the newest id the first read saw, so none falls between the two. gone: the
// participant's session ended and its announcement went with it ([GONE_WITH_ITS_WRITER_V1]).
void capable(Space& sp, const std::string& directory, std::function<bool(const std::string& capability)> match,
             std::function<void(const Capable&)> on);

// ---- the last value -----------------------------------------------------------------------------------------------
// (space, topic, <writer>): the writer's own last value on the topic
void offer(Space& sp, const std::string& space, const std::string& topic, const std::string& writer, const std::string& bag);
void offer(Space& sp, const std::string& space, const std::string& topic, const std::string& writer,
           const std::vector<std::string>& order, semcodec::Fields fields);
// the newest of every writer's last value on the topic (by write id); false: nobody has offered one
bool latest(Space& sp, const std::string& space, const std::string& topic, SpaceRow& out);

// ---- ask / answer -------------------------------------------------------------------------------------------------
// The asker's requests are (<service>.ask, <asker>, <key>); the replies to it are (<service>.answer, <asker>, <key>).
class Asker {
public:
    // on_reply: each reply written to this asker, in write order, on its read's own thread
    // ended: its replies' read ended (the session is gone), said once
    Asker(Space& sp, std::string service, std::string me, std::function<void(const SpaceRow&)> on_reply,
          std::function<void(const std::string& why)> ended = {});
    void ask(const std::string& key, const std::string& bag);                                          // written: done
    void ask(const std::string& key, const std::vector<std::string>& order, semcodec::Fields fields);
    const std::string& me() const { return me_; }
private:
    Space& sp_; std::string service_, me_;
};
class Answerer {
public:
    // on_request: every asker's every request, in each asker's write order, on the answerer's read's own thread. A
    // request with gone set: the asker's session ended and its requests went with it -- the asker is gone
    // ([GONE_WITH_ITS_WRITER_V1]).
    Answerer(Space& sp, std::string service, std::function<void(const std::string& asker, const SpaceRow&)> on_request);
    void answer(const std::string& asker, const std::string& key, const std::string& bag);              // written: done
    void answer(const std::string& asker, const std::string& key, const std::vector<std::string>& order, semcodec::Fields fields);
private:
    Space& sp_; std::string service_;
};
// the asker's request cells, (<service>.ask, <asker>, <key>), and the replies to it, (<service>.answer, <asker>, <key>)
inline std::string ask_space(const std::string& service) { return service + ".ask"; }
inline std::string answer_space(const std::string& service) { return service + ".answer"; }

}  // namespace ribbit::pp
