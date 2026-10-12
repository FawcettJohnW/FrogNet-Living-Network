// transport.hpp -- [TRANSPORT_INTERFACE_V1] (plan item 2) the transport interface: DEFINITIONS ONLY, no operations.
//
// John, 2026-10-07: the transport (FrogNet, FD&F, InfiniBand, whatever) owns discovery, name resolution and internal
// routing; Ribbit binds to interface definitions only, never to a transport's operations. The abstraction is
// intentional and fungible: anything that answers these is a transport Ribbit can run on.
//
// No definition carries a route, a path or a neighbour. A transport that routes internally (FrogNet) keeps it inside.
#pragma once
#include <memory>
#include <string>
#include <vector>

namespace transport {

// One participant, as the transport knows it.
struct Participant {
    std::string id;                    // the transport's identity for the machine (stable while it runs)
    std::string name;                  // its name (FD&F: a RINA name -- the machine's hostname)
    std::vector<std::string> ips;      // its addresses (FD&F: LISP locators, stated by the machine itself)
    std::string capability;            // its measured capability, as JSON (FD&F: FrogNet's capability probe, verbatim)
    bool alive = false;                // its connection to the RAM manager is up
};

// The socket a machine uses to reach the shared space (FD&F: the FNWP session to the RAM manager).
class Socket {
public:
    virtual ~Socket() = default;
    virtual std::string call(const std::string& request) = 0;     // one request, its answer
    virtual void post(const std::string& request) = 0;            // one request, no answer awaited
};

class Transport {
public:
    virtual ~Transport() = default;
    // LOCATE: given the area's name, where its RAM manager is ("host:port").
    virtual std::string locate(const std::string& area) = 0;
    // CONNECT: this machine's socket to that RAM manager. Liveness is the socket.
    virtual std::unique_ptr<Socket> connect(const std::string& where) = 0;
    // ANNOUNCE: this machine's announcement (name, addresses, capability) into the shared space.
    virtual void announce(Socket& s, const Participant& self) = 0;
    // PARTICIPANTS: every participant the shared space holds, alive or not.
    virtual std::vector<Participant> participants(Socket& s) = 0;
    // CAPABILITY: one participant's capability (JSON), for elections and placement.
    virtual std::string capability(Socket& s, const std::string& id) = 0;
    // CHANGE: wait until the participant set changes after `after` (a generation the previous call returned);
    // returns the new generation. Held, never polled.
    virtual unsigned long long change(Socket& s, unsigned long long after) = 0;
};

}  // namespace transport
