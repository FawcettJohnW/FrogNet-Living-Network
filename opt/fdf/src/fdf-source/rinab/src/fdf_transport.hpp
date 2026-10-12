// fdf_transport.hpp -- [TRANSPORT_INTERFACE_V1] FD&F answering src/transport.hpp's definitions.
//   locate       the area's RAM manager: rina_region::Client::locate() (RINA_RAM, or the area's name through LISP)
//   connect      the FNWP session to it: rina_region::Client(where)
//   announce     op announce: name, addresses, capability
//   participants op participants: every announcement, alive = its machine's session is up
//   capability   one participant's capability, from the same answer
//   change       op watch_participants once, then a held wait on this process's own input space
#pragma once
#include "transport.hpp"
#include "rina_region_client.hpp"
#include <unistd.h>

namespace fdf {

// frogram::Json has no serialiser; this writes one back as JSON text (numbers as the shortest exact form)
inline std::string dump(const frogram::Json& j) {
    switch (j.type) {
        case frogram::Json::Null: return "null";
        case frogram::Json::Bool: return j.b ? "true" : "false";
        case frogram::Json::Num: { char b[40]; snprintf(b, sizeof b, "%.17g", j.n); return b; }
        case frogram::Json::Str: return frogram::Json::quote(j.s);
        case frogram::Json::Arr: { std::string o = "["; for (size_t i = 0; i < j.a.size(); ++i) o += (i ? "," : "") + dump(j.a[i]); return o + "]"; }
        case frogram::Json::Obj: { std::string o = "{"; for (size_t i = 0; i < j.o.size(); ++i) o += (i ? "," : "") + frogram::Json::quote(j.o[i].first) + ":" + dump(j.o[i].second); return o + "}"; }
    }
    return "null";
}

class Socket : public transport::Socket {
public:
    explicit Socket(const std::string& where) : c_(where) {}
    std::string call(const std::string& request) override { return dump(c_.call(request)); }
    void post(const std::string& request) override { c_.post(request); }
    rina_region::Client& client() { return c_; }
private:
    rina_region::Client c_;
};

class Transport : public transport::Transport {
public:
    std::string locate(const std::string& area) override {
        if (!area.empty()) setenv("RINA_AREA", area.c_str(), 1);      // the name given; resolved by the transport's rules
        return rina_region::Client::locate();
    }
    std::unique_ptr<transport::Socket> connect(const std::string& where) override { return std::make_unique<Socket>(where); }
    void announce(transport::Socket& s, const transport::Participant& self) override {
        std::string ips = "["; bool f = true; for (auto& a : self.ips) { ips += (f ? "" : ",") + frogram::Json::quote(a); f = false; } ips += "]";
        frogram::Json r = sock(s).client().call("{\"op\":\"announce\",\"ns\":" + frogram::Json::quote(self.id) + ",\"name\":" + frogram::Json::quote(self.name) +
                                                ",\"ips\":" + ips + ",\"cap\":" + frogram::Json::quote(self.capability) + "}");
        if (!r["ok"].b) throw std::runtime_error("announce refused: " + r["error"].s + " " + r["why"].s);
    }
    std::vector<transport::Participant> participants(transport::Socket& s) override {
        frogram::Json r = sock(s).client().call("{\"op\":\"participants\"}");
        if (!r["ok"].b) throw std::runtime_error("participants refused: " + r["error"].s);
        std::vector<transport::Participant> out;
        for (auto& p : r["participants"].a) {
            transport::Participant x; x.id = p["ns"].s; x.name = p["name"].s; x.alive = p["alive"].b;
            for (auto& a : p["ips"].a) x.ips.push_back(a.s);
            x.capability = dump(p["cap"]); out.push_back(x);
        }
        return out;
    }
    std::string capability(transport::Socket& s, const std::string& id) override {
        for (auto& p : participants(s)) if (p.id == id) return p.capability;
        throw std::runtime_error("no participant " + id);
    }
    unsigned long long change(transport::Socket& s, unsigned long long after) override {
        const std::string me = "watch:" + std::to_string(getpid());
        if (!watching_) { frogram::Json r = sock(s).client().call("{\"op\":\"watch_participants\",\"proc\":" + frogram::Json::quote(me) + "}");
                          if (!r["ok"].b) throw std::runtime_error("watch refused: " + r["error"].s);
                          watching_ = true; }
        // held until an event past `after` is in this process's input space; the answer is "E\n" then "<n>\t<event>\n" lines
        const std::string text = sock(s).client().held_text("{\"op\":\"wait\",\"proc\":" + frogram::Json::quote(me) + ",\"after\":" + std::to_string(after) + "}", 3.0e8);
        unsigned long long last = after;
        for (size_t p = 2; p < text.size();) { const size_t tab = text.find('\t', p), nl = text.find('\n', p);
            if (tab == std::string::npos || nl == std::string::npos) break;
            const unsigned long long n = std::stoull(text.substr(p, tab - p)); if (n > last) last = n; p = nl + 1; }
        return last;
    }
private:
    static Socket& sock(transport::Socket& s) { return static_cast<Socket&>(s); }
    bool watching_ = false;
};

}  // namespace fdf
