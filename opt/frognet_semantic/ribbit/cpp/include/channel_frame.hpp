// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// channel_frame.hpp -- [THE_FRAME_IS_A_LIBRARY_V1] the protocol layer's frame, for any permanently open connection.
//
// John, 2026-11-24: "the communicator can use the keys and Cypher and encode the video and audio across the wire.
// They would be able to switch protocols and etc. Without interrupting video." "Make it a library kind of thing so it
// can apply to anything."
//
// This is core/channel_frame.py's frame (build channel-frame-v13), byte for byte, with no socket in it. A Codec is
// one end of one connection. The program that owns the connection hands it a message and gets back the bytes to put
// on the connection; it hands it the bytes that arrived and gets back the messages. So it goes under a program that
// already has its own way of writing a socket (the Communicator's Plane, which sheds and aborts frames), as well as
// under one that has none. The same bytes cross a connection whose other end is core/channel_frame.py's Link.
//
//     on the connection   <length> <packet ID> <the data, sealed under the Role's current key, or not sealed>
//     the data, opened    <the protocol's word, 4 bytes> <what that protocol's driver packed>
//
//   * A Role's SETTING says its protocol (driver), the number its key is computed from (0: not sealed) and the
//     algorithm. The program gives the Codec the setting it holds, and gives it again whenever its held read on the
//     setting fires (setting()). The Codec holds no read and asks nobody.
//   * The KEY is computed, never stored and never sent: the first 32 bytes of HMAC-SHA384(the Role's secret on this
//     machine, "frognet channel key\n" + the SENDING node's FNID + "\n" + the Role + "\n" + the number).
//   * A SWITCH travels in the stream: the sender's next unit after its setting changed begins with the call (SWP or
//     SWE), sealed the old way, and everything after it is the new way. Nothing is closed or opened for it.
//   * A frame that does not open goes back for a retry (NOT OPENED, AGAIN), as in core/channel_frame.py.
//   * One rule here is not in core/channel_frame.py: when the setting this end holds for a Role is sealed, an
//     unsealed MESSAGE for it is not taken (it goes back for a retry the same way). There, a sender that names its
//     sequence unsealed has its unsealed messages taken whatever the receiver's setting says.
//
// Threads. One thread writes a connection and one reads it; they may be different threads. pack(), next() and undo()
// are the writer's. feed() is the reader's. setting() is anyone's. Nothing here locks a socket: the only guarded
// things are this end's copy of the settings and the list of what the reader hands the writer, inside one process.
//
// Needs only the machine's libcrypto (OpenSSL), as core/channel_protection.py does.
#pragma once
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace channel_frame {

constexpr const char* BUILD = "channel-frame-cpp-v6-20261124f (the frame of channel-frame-v13-20261123f)";
using Bytes = std::vector<uint8_t>;

// The connection cannot go on (core/transport_driver.py's ConnectionGone): the reason is what().
struct Gone : std::runtime_error { using std::runtime_error::runtime_error; };
// This machine cannot make the key or the check: no secret, no FNID, an algorithm it has not registered.
struct DoesNotOpen : Gone { using Gone::Gone; };

constexpr const char* DEFAULT_PROTECTION = "aes-256-gcm";
constexpr const char* DEFAULT_SECRETS = "/etc/frognet/channel_secrets";
constexpr size_t MAX_FRAME = size_t(64) << 20;
constexpr int STREAMS = 256;
constexpr int SAY_RECEIVED_EVERY = 64;
constexpr size_t TAG = 16;

struct Setting {
    std::string driver = "tcp";                    // the protocol: a registered driver's name
    uint64_t number = 0;                           // 0: not sealed
    std::string protection = DEFAULT_PROTECTION;   // the algorithm
    long step = -1;                                // the step of the Role's own secret; -1: the setting names none
};

struct Message {
    std::string role;
    int sequence = 0;
    uint64_t counter = 0;
    bool reply = false;
    Bytes data;
};

// ---- the secret, the key, the check: the computations of core/channel_frame.py and core/channel_setting.py
struct Secret { Bytes value; long step = 0; bool own = false; };
Secret read_secret(const std::string& role, const std::string& dir);                      // the file, as it is
Secret secret_at(const std::string& role, long step, const std::string& dir);             // in memory; step -1: as the file is
Bytes next_secret(const Bytes& secret, const std::string& role, long step);
bool keep_secret(const std::string& role, const Bytes& secret, long step, const std::string& dir);   // moves the file forward
Bytes key_for(const Bytes& secret, const std::string& fnid, const std::string& role, uint64_t number);
std::string secret_fingerprint(const Bytes& secret);
// The text a setting's check is made over: the Role's name, then each other field as name=value, sorted by name.
// `fields` holds every field of the setting but "check" and "ts", each value as Python prints it (true/false for a
// boolean). check_of is HMAC-SHA384 of it keyed with the Role's secret, 96 hex characters.
std::string check_text(const std::string& role, const std::map<std::string, std::string>& fields);
std::string check_of(const Bytes& secret, const std::string& role, const std::map<std::string, std::string>& fields);
std::string fnid_here();                                                                   // /etc/fnid (FROGNET_GUID_FILE)

// A setting as a program read it from its tuple, taken or ignored ([A_SETTING_PROVES_ITSELF_V1],
// [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1]; core/channel_setting.py _proved). `fields` is every field of the tuple's
// JSON, "check" included, each value as text. A machine that holds the Role's secret takes the setting only if its
// check verifies under the secret at the setting's step, and then moves its own file to that step. A machine with no
// secret for the Role has nothing to verify with: it takes the setting and cannot open the Role's sealed frames.
struct Taken { bool taken = false; bool moved = false; std::string why, fingerprint; Setting setting; };
Taken take_setting(const std::string& role, const std::map<std::string, std::string>& fields, const std::string& dir);

// ---- the algorithms (core/channel_protection.py): name -> seal, open. open returns false when it does not open.
using SealFn = std::function<Bytes(const Bytes& key, const uint8_t nonce[12], const uint8_t aad[8], const uint8_t* p, size_t n)>;
using OpenFn = std::function<bool(const Bytes& key, const uint8_t nonce[12], const uint8_t aad[8], const uint8_t* p, size_t n, Bytes& out)>;
void register_protection(const std::string& name, SealFn seal, OpenFn open);
bool protection_known(const std::string& name);

// ---- the protocols under the frame: the word a setting names -> its 4-byte word, pack, unpack.
// `state` is the protocol's own, for one Role in one direction of one connection: empty at the start and after a switch.
using DriverState = std::map<std::string, Bytes>;
using PackFn = std::function<Bytes(DriverState& state, uint64_t packet_id, const Bytes& message)>;
using UnpackFn = std::function<Bytes(DriverState& state, uint64_t packet_id, const uint8_t* p, size_t n)>;
void register_driver(const std::string& name, const char word[4], PackFn pack, UnpackFn unpack);   // tcp, chorus, openqasm are there
bool driver_known(const std::string& name);

struct Counts {            // what this end has put on and taken off the connection, and its calls [sent, received]
    uint64_t sent = 0, received = 0, not_opened = 0, sent_again = 0;
    uint64_t protocol[2] = {0, 0}, encryption[2] = {0, 0}, not_opened_calls[2] = {0, 0}, again[2] = {0, 0}, received_calls[2] = {0, 0};
};

class Codec {
public:
    using Say = std::function<void(const std::string&)>;
    // opener: true at the end that opened the connection (it gives each Role its sequence). fnid: this node's FNID
    // ("" reads /etc/fnid when a sealed Role first needs it). secrets: the directory of the Roles' secrets.
    // far: the other end's name, for what is said.
    Codec(bool opener, std::string far, std::string fnid = "", std::string secrets = DEFAULT_SECRETS, Say say = nullptr);

    // Anyone: the setting this process holds for a Role (nullopt: it holds none). Called when it first has one and
    // whenever its held read on the setting fires. The switch goes into the stream at the writer's next unit.
    void setting(const std::string& role, std::optional<Setting> s);
    // Called (by whoever hands it, never under a lock of this Codec's) each time something is handed to the writer: a
    // setting changed, or the reader read something that makes this end owe the far end a call. A program whose
    // writer waits when it has nothing to send wakes it here, so a switch goes into the stream though nothing else is.
    void when_owing(std::function<void()> wake) { wake_ = std::move(wake); }

    // The writer. Each returns one unit: bytes to put on the connection, whole and in order.
    //   pack()   one message for a Role, behind whatever calls this end owes (a switch, RECEIVED). false, and no
    //            unit: this end is sending messages again because the far end asked; take next() until it has no
    //            more, then pack. A program that cannot wait drops the message, as it would on a full connection.
    //   next()   what this end owes the far end without being asked: calls, and messages sent again, one a unit.
    //            false: nothing. A program that is not sending calls this after feed(), so its calls still go.
    //   undo()   the unit last returned did NOT go on the connection (none of it): this end is as it was before it.
    bool pack(const std::string& role, const Bytes& message, Bytes& unit);
    bool pack_reply(const std::string& role, int sequence, uint64_t counter, const Bytes& message, Bytes& unit);
    bool next(Bytes& unit);
    void undo();

    // The reader: bytes as they arrived, in order, in any pieces. Messages are added to `out` in the order opened.
    void feed(const uint8_t* p, size_t n, std::vector<Message>& out);

    std::map<std::string, std::string> running();     // Role -> "driver=.. number=.. protection=.. sealed=.." (the writer's)
    Counts counts() const;
    std::string far() const { return far_; }

private:
    struct Kept { uint64_t pid; bool reply; std::shared_ptr<const Bytes> message; };
    struct Side {
        std::string role, fnid, protection = DEFAULT_PROTECTION;
        std::string word;                           // 4 bytes, or empty
        uint64_t number = 0, count = 0, got = 0, skip = 0, base = 0, since = 0;
        bool sealed = false, named = false, owed = false, lost = false, spoke = false;
        Bytes key;
        DriverState state;
        std::vector<Kept> kept;
    };
    struct Item { int kind; int stream; uint64_t n; bool flag; std::string role; };   // what the reader (or anyone) hands the writer
    struct Owed { int kind; int stream; uint64_t n; bool started; std::string role; };
    struct Out {                                    // everything the writer changes: copied whole for undo()
        std::map<int, Side> side;
        std::map<std::string, int> stream;          // a Role's sequence
        std::map<int, std::string> in_role;         // the sequences the far end has named, as the reader said
        std::map<int, uint64_t> count, parked;
        std::set<int> asked;
        std::deque<Owed> owed;
        int last = -1;
        Counts counts;
    };

    std::optional<Setting> setting_of(const std::string& role);
    void hand(Item i);
    void take_handed();
    void put(Bytes& unit, uint64_t pid, const Bytes& body);
    Bytes seal(Side& s, uint64_t pid, const Bytes& data);
    void rekey(Side& s, uint64_t number, const std::string& protection);
    std::pair<int, Side*> out_side(Bytes& unit, const std::string& role, int stream);
    void call(Bytes& unit, int stream, Side& s, const Bytes& data);
    void catch_up(Bytes& unit, int stream, Side& s);
    void message(Bytes& unit, Side& s, uint64_t pid, const Bytes& m);
    void ask_again(Bytes& unit, int stream);
    bool one(Bytes& unit, const std::string& role, int stream, uint64_t counter, bool reply, const Bytes& m);
    bool owed_into(Bytes& unit, bool calls_only);
    void say(const std::string& text) const { if (say_) say_(text); }
    const std::string& mine();
    // the reader's
    bool take(uint64_t& pid, Bytes& body);
    void frame(uint64_t pid, const Bytes& body, std::vector<Message>& out);
    bool open(Side& s, uint64_t pid, const Bytes& body, Bytes& data);
    void rekey_in(Side& s, uint64_t number, const std::string& protection);

    const bool opener_;
    const std::string far_, secrets_;
    std::string fnid_;
    Say say_;
    std::function<void()> wake_;
    std::mutex guard_;                              // this end's settings and what is handed to the writer; nothing else
    std::map<std::string, std::optional<Setting>> settings_;
    std::deque<Item> handed_;
    Out o_, before_;
    bool can_undo_ = false;
    uint8_t nonce_head_[8];
    uint32_t nonce_n_ = 0;
    // the reader's
    std::map<int, Side> in_;
    std::map<int, uint64_t> count_in_;
    int last_in_ = -1;
    Bytes buf_;
    size_t at_ = 0;
    uint64_t received_ = 0, not_opened_ = 0, calls_in_[5] = {0, 0, 0, 0, 0};
};

}  // namespace channel_frame
