// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [THE_CALL_IS_UNDER_THE_FRAME_V1] The Communicator's channel: how a call's audio and video are carried.
//
// A call's planes are for one Role (by default "mediahost"). The Role's setting says the protocol its bodies are
// packed by, the number its key is computed from and the algorithm, exactly as a Role's setting does on a FrogNet
// node (core/channel_setting.py), and it is a tuple in the Communicator's own memory:
//
//     (channel, setting, <Role>)   {driver, number, protection, step, ram_host, ram_port, check}
//                                  its owner: whoever runs comms-channel or uses a window's Security tab
//     (channel, at, <Role>:<end>)  {role, end, driver, number, protection, step, sealed, framed, ram, ignored, build}
//                                  each process's report: the setting it is running, or the one it ignored and why.
//                                  It is the process's own tuple and goes when the process does. It holds no key and
//                                  no secret. comms-channel's write returns when every end's report shows the
//                                  setting, or says which end ignored it (core/channel_setting.py's (channel, at, *)).
//
// "number" is written as text, because it is a 62-bit number and the memory's JSON keeps numbers as doubles.
//
// [THE_MEMORY_MOVES_V1] ram_host and ram_port, when the setting has them, say where the Communicator's memory is. They
// are under the setting's check like every other field. A process that reads a setting naming another address than
// the one it is attached to follows it (ram_at.hpp). Its owner writes the setting at the new address first, then at
// the old one, so a process that arrives finds a setting that names where it already is. Every Communicator process
// holds this read, whether its planes are under the frame or not, and every one reports.
//
// Each process has one Channel. It holds the setting (ChannelWatch holds the read on the tuple), proves each one it
// reads with the Role's secret on this machine (the check; a setting that does not verify is ignored and the one held
// is kept), and tells every plane's end of the frame, so each switches in its own stream. A plane made later starts
// from the setting held then. Nothing is closed, opened or sent to anyone for a switch.
//
// The secret is a file on the machine, placed by hand: <secrets>/<Role>, or <secrets>/default for a Role with none of
// its own (/etc/frognet/channel_secrets, the node's own directory; FROGNET_CHANNEL_SECRETS names another). A machine
// with no secret takes any setting and cannot open or send the Role's sealed frames.
#pragma once
#include "channel_frame.hpp"
#include "room.hpp"
#include <atomic>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace comms {

constexpr const char* CHANNEL_SERVICE = "channel";
constexpr const char* CHANNEL_VAR = "setting";
constexpr const char* CHANNEL_AT = "at";
constexpr const char* CHANNEL_ROLE = "mediahost";
constexpr int CHANNEL_REPORT_S = 10, CHANNEL_REPORT_FRESH_S = 25;   // an end writes its report at least this often; older than this, it is gone

std::string channel_secrets_dir();                           // FROGNET_CHANNEL_SECRETS, or the node's directory
std::string channel_sender_id(const std::string& me);        // this node's FNID (/etc/fnid); a machine that is no node: "comms-<me>"
std::map<std::string, std::string> channel_fields(const frogram::Json& value);   // a setting's JSON, each value as text
std::string channel_value_json(const std::map<std::string, std::string>& fields); // and back, for the one who writes it

class Channel {
public:
    Channel(std::string role, std::string sender_id, std::string secrets, bool framed = true);
    bool framed() const { return framed_; }                   // this program's planes are under the frame
    std::map<std::string, std::string> fields() const;        // the setting's tuple as last taken (empty: none)
    std::string ignored() const;                              // why the last setting read was ignored ("" when it was taken)
    const std::string& role() const { return role_; }
    const std::string& secrets() const { return secrets_; }
    // A new connection's end of the frame, holding the setting this process holds now.
    std::shared_ptr<channel_frame::Codec> codec(bool opener, const std::string& far);
    // The setting's tuple as just read (nullopt: there is none). Proved, kept, and every connection told.
    void read(const std::optional<std::map<std::string, std::string>>& fields);
    std::string held() const;                                 // what this process holds, for a screen or a log
    std::string report_json() const;                          // this process's report: what it runs, what it last ignored
    std::string end() const { return sender_ + "." + std::to_string(pid_); }
    bool sealed() const;                                      // the setting this process holds is sealed
    uint64_t changes() const { return changes_.load(); }      // how many settings this process has taken
    // [THE_OVERLAY_SHOWS_THE_CHANNEL_V1] for a screen: what this process's planes have done under the frame, which key
    // it sends under (8 characters of a hash of it, never the key; "" when not sealed), and the last change it took.
    channel_frame::Counts totals() const;                     // summed over this process's planes
    std::string key_tag() const;
    struct Change { std::string what; std::string at; double age_s = -1; };   // what changed, the wall clock then, seconds since
    Change last_change() const;

    static void set_for_process(std::shared_ptr<Channel> c);  // the program's main: every plane it makes is under the frame
    static std::shared_ptr<Channel> of_process();             // null: this program's planes are as they were
    static std::shared_ptr<Channel> watching();               // the process's channel, framed or not (null before its main made it)
private:
    const std::string role_, sender_, secrets_;
    const bool framed_;
    std::map<std::string, std::string> fields_;
    mutable std::mutex mu_;
    std::optional<channel_frame::Setting> held_;
    std::vector<std::weak_ptr<channel_frame::Codec>> codecs_;
    std::atomic<uint64_t> changes_{0};
    std::string ignored_;                                     // why the last setting read was ignored ("" when it was taken)
    std::string key_tag_, changed_what_, changed_at_;
    std::chrono::steady_clock::time_point changed_when_{};
    mutable channel_frame::Counts gone_;                      // the counts of planes that have closed, as last seen
    mutable std::vector<std::pair<std::weak_ptr<channel_frame::Codec>, channel_frame::Counts>> seen_;
    const long pid_;
};

// The read this process holds on its Role's setting, on a session of its own.
class ChannelWatch {
public:
    ChannelWatch(std::shared_ptr<Channel> channel, const std::string& ram_host, int ram_port);
    ~ChannelWatch();
    bool read_once() const { return read_once_.load(); }     // the setting as it stood has been read
private:
    std::shared_ptr<Channel> channel_;
    CommsClient c_;
    std::atomic<bool> stop_{false}, read_once_{false}, moved_{false};
    int moved_id_ = 0;
    std::thread th_;
};

// A program's main. role: its --channel option. "" = not given: its planes are as they were; it still holds the read
// on the default Role's setting, because that is how it follows the memory when the memory moves, and it reports.
// Makes the process's Channel; with a memory (ram_host not ""), holds the read on the Role's setting there and returns
// once the setting as it stands has been read, so no plane is opened before this process knows whether the Role is
// sealed. Throws if the memory does not answer in 10 s.
struct ProgramChannel { std::shared_ptr<Channel> channel; std::unique_ptr<ChannelWatch> watch; };
ProgramChannel channel_for_program(const std::string& role, const std::string& me, const std::string& ram_host, int ram_port);

// ---- the owner's work: comms-channel and a window's Security tab both call these ------------------------------
// One end's report, as the memory holds it.
struct ChannelEnd { std::string end; std::map<std::string, std::string> report; };
struct ChannelNow {                                           // what `show` shows
    std::map<std::string, std::string> setting;               // the Role's setting ("check" left out); empty: none
    std::vector<ChannelEnd> ends;
};
ChannelNow channel_show(const std::string& ram_host, int ram_port, const std::string& role);

// One change by the owner. Exactly one of: protocol; seal / unseal / new_key / algorithm; move_host.
struct ChannelChange {
    std::string protocol, algorithm;
    bool seal = false, unseal = false, new_key = false;
    std::string move_host; int move_port = 0;                 // [THE_MEMORY_MOVES_V1] the memory's new address
};
struct ChannelDone {
    std::map<std::string, std::string> setting;               // as written
    bool checked = true;                                      // false: this machine holds no secret, so the setting carries no check
    bool moved = false;                                       // the change was the memory's address
    std::vector<std::string> lines;                           // one for each end that ignored it or has not taken it
    int ends = 0, running = 0, ignoring = 0, behind = 0, chat_carried = 0;
    double ms = 0;                                            // from the write until every end had answered (or the wait ran out)
    std::string summary() const;                              // "3 end(s) there: 3 running it (9 ms after the write)"
};
// Writes the Role's setting with the change made and returns when every end's report shows it, or says it ignored it,
// or wait_s has passed. A move writes the setting at the new address first (and carries the chat there, which has no
// owner to assert it), then at the old one, and waits for every end that was at the old address to report at the new.
// Throws std::runtime_error with the reason when the change cannot be made.
ChannelDone channel_change(const std::string& ram_host, int ram_port, const std::string& role, const ChannelChange& change,
                           double wait_s = 10.0, const std::string& secrets = channel_secrets_dir());
// This machine's secret for the Role, as a screen may show it: "the network's default, hash 5a78a64b" (throws if none).
std::string channel_secret_line(const std::string& role, const std::string& secrets = channel_secrets_dir());

}  // namespace comms
