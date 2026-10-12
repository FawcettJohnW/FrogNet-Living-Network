// unrest_handler.hpp -- C++ form of core/unrest_handler.py: the ONE handler interface. Every handler is the same
// shape -- identity, a codec slot, an election slot, a lifecycle slot -- and a caller looks the handler up and calls
// the interface; it never branches on type.
//
// As in the Python:
//   - the codec slot defaults are typed empties, so a handler that is not a codec is inert on that path. An empty
//     extraction is a real answer to callers (the daemon sends RESP_RAW on it), not a swallowed failure;
//   - the election slot is PURE: a function of the candidates it is handed; no I/O, no probes, no threads;
//   - the lifecycle slot does real I/O for a handler that names a role: it writes <role>/capability.
// Different from the Python, and stated rather than hidden:
//   - advertise() writes Ribbit memory (frogram::Memory: service=<role>, variable="capability",
//     instance=host:<ip>:<role>), not the api.php tuple store the Python writes through core/frognet_tuples.py, so the
//     Python election does not read it;
//   - advertise() takes the capability blob; it does not run the probe itself, and a write that fails raises (the
//     Python logs and continues).
#pragma once

#include "frogram.hpp"
#include "semcodec.hpp"

#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace frognet {

using frogram::Json;

class UnRESTHandler {
public:
    virtual ~UnRESTHandler() = default;

    // ---- identity: a content-only handler names no role
    virtual const char* role_name() const { return nullptr; }
    virtual const char* candidate_type() const { return nullptr; }
    virtual int default_port() const { return 0; }
    std::string mode() const { return role_name() ? role_name() : ""; }

    // ---- codec slot (typed empties by default)
    virtual pyv::Dict learn_request_template(std::string_view) { return {}; }
    virtual pyv::Dict learn_reply_template(std::string_view) { return {}; }
    virtual semcodec::Fields extract_request_dynamic(std::string_view, const pyv::Dict&) { return {}; }
    virtual semcodec::Fields extract_reply_dynamic(std::string_view, const pyv::Dict&) { return {}; }
    virtual std::string rebuild_reply(const pyv::Dict&, const pyv::List&) { return ""; }
    virtual std::string decode_payload(std::string_view) { return ""; }

    // ---- election slot: pure. -1 = ineligible; a content handler never wins a role.
    virtual double score(const Json&) const { return -1.0; }
    virtual const Json* evaluate(const std::vector<Json>& hosts_list, const std::vector<Json>& lan_list) const;

    // ---- lifecycle slot: real for a handler that names a role
    std::string role_scope(const std::string& my_ip) const;
    // Writes <role>/capability under role_scope(my_ip) to `control`, and to `data` when the data host is a different
    // machine (pass nullptr when it is the same one). Returns what was written ("<role>@control", "<role>@data").
    // No role: writes nothing and returns {}. A failed write raises.
    std::vector<std::string> advertise(frogram::Memory& control, frogram::Memory* data, const std::string& my_ip,
                                       const std::string& capability_json) const;
    std::vector<std::string> hostReset(frogram::Memory& control, frogram::Memory* data, const std::string& my_ip,
                                       const std::string& capability_json) const {
        return advertise(control, data, my_ip, capability_json);
    }
};

}  // namespace frognet

namespace frognet {
inline const Json* UnRESTHandler::evaluate(const std::vector<Json>&, const std::vector<Json>&) const { return nullptr; }
inline std::string UnRESTHandler::role_scope(const std::string& my_ip) const {
    if (!role_name()) throw std::logic_error("role_scope: a content-only handler names no role");
    if (my_ip.empty()) throw std::invalid_argument(std::string("role_scope: no node address for role ") + role_name());
    return "host:" + my_ip + ":" + role_name();
}
inline std::vector<std::string> UnRESTHandler::advertise(frogram::Memory& control, frogram::Memory* data, const std::string& my_ip,
                                                         const std::string& capability_json) const {
    if (!role_name()) return {};
    const std::string scope = role_scope(my_ip);
    std::vector<std::string> written;
    control.write(role_name(), "capability", scope, capability_json);   // raises on failure, naming the far end
    written.push_back(std::string(role_name()) + "@control");
    if (data) {
        data->write(role_name(), "capability", scope, capability_json);
        written.push_back(std::string(role_name()) + "@data");
    }
    return written;
}
}  // namespace frognet
