#include "version.hpp"
// lisp_handler_driver -- drives frognet::LispHandler for tools/test_lisp_handler.py. One {"operation","args"} per
// stdin line, one {"ok","result"|"error"} per stdout line (the ribbit-lisp convention). Usage: --ram HOST PORT
#include "lisp_handler.hpp"

#include <iostream>
#include <sstream>

using frognet::Json;

static std::string dump(const Json& j) {  // frogram::Json has no writer; enough of one for the test
    switch (j.type) {
    case Json::Null: return "null";
    case Json::Bool: return j.b ? "true" : "false";
    case Json::Num: { std::ostringstream o; o.precision(17); o << j.n; return o.str(); }
    case Json::Str: return Json::quote(j.s);
    case Json::Arr: { std::string r = "["; for (size_t i = 0; i < j.a.size(); ++i) r += (i ? "," : "") + dump(j.a[i]); return r + "]"; }
    case Json::Obj: {
        std::string r = "{";
        for (size_t i = 0; i < j.o.size(); ++i) r += (i ? "," : "") + Json::quote(j.o[i].first) + ":" + dump(j.o[i].second);
        return r + "}";
    }
    }
    throw std::logic_error("dump: bad type");
}
static std::string str(const Json& a, const char* k) {
    if (a[k].type != Json::Str) throw std::runtime_error(std::string("driver: missing string arg ") + k);
    return a[k].s;
}
static pyv::Dict dict_of(const std::string& text) { return semcodec::json_loads(text).as<pyv::Dict>(); }

int main(int argc, char** argv) {
    if (argc != 4 || std::string(argv[1]) != "--ram") { std::cerr << "usage: lisp_handler_driver --ram HOST PORT\n"; return 2; }
    const std::string host = argv[2]; const int port = std::stoi(argv[3]);
    frognet::LispHandler h;
    std::unique_ptr<frogram::Session> sess;
    std::unique_ptr<frogram::Memory> mem;
    std::unique_ptr<frognet::LispSite> site;
    std::unique_ptr<frognet::LispBoundary> boundary;
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            Json r = Json::parse(line); const std::string op = r["operation"].s; const Json& a = r["args"];
            std::string out;
            if (op == "identity") {
                out = "{\"role\":" + Json::quote(h.role_name()) + ",\"candidate\":" + Json::quote(h.candidate_type()) +
                      ",\"port\":" + std::to_string(h.default_port()) + "}";
            } else if (op == "score") {
                std::ostringstream o; o.precision(17); o << h.score(a["cand"]); out = o.str();
            } else if (op == "evaluate") {
                std::vector<Json> hosts = a["hosts"].a, lan = a["lan"].a;
                const Json* w = h.evaluate(hosts, lan);
                if (!w) out = "null";
                else for (size_t i = 0; i < hosts.size(); ++i) if (w == &hosts[i]) out = "{\"list\":\"hosts\",\"index\":" + std::to_string(i) + "}";
                if (out.empty()) for (size_t i = 0; i < lan.size(); ++i) if (w == &lan[i]) out = "{\"list\":\"lan\",\"index\":" + std::to_string(i) + "}";
                if (out.empty()) throw std::logic_error("evaluate returned a candidate from neither list");
            } else if (op == "advertise") {
                if (!mem) { sess.reset(new frogram::Session(host, port, LISPER_API)); mem.reset(new frogram::Memory(*sess, LISPER_API)); }
                auto w = h.advertise(*mem, nullptr, str(a, "ip"), str(a, "capability"));
                out = "["; for (size_t i = 0; i < w.size(); ++i) out += (i ? "," : "") + Json::quote(w[i]); out += "]";
            } else if (op == "memory.read") {
                if (!mem) { sess.reset(new frogram::Session(host, port, LISPER_API)); mem.reset(new frogram::Memory(*sess, LISPER_API)); }
                auto cells = mem->read(str(a, "service"), str(a, "variable"), str(a, "instance"));
                out = "[";
                for (size_t i = 0; i < cells.size(); ++i)
                    out += (i ? "," : "") + std::string("{\"instance\":") + Json::quote(cells[i].instance) + ",\"bag\":" + dump(cells[i].bag) + "}";
                out += "]";
            } else if (op == "learn") {
                bool reply = a["reply"].type == Json::Bool && a["reply"].b;
                pyv::Dict f = reply ? h.learn_reply_template(str(a, "body")) : h.learn_request_template(str(a, "body"));
                out = semcodec::json_dumps(pyv::Obj(f));
            } else if (op == "extract") {
                bool reply = a["reply"].type == Json::Bool && a["reply"].b;
                pyv::Dict frag = dict_of(str(a, "fragment"));
                auto f = reply ? h.extract_reply_dynamic(str(a, "body"), frag) : h.extract_request_dynamic(str(a, "body"), frag);
                pyv::List l; for (auto& kv : f) l.push_back(pyv::List{pyv::Str{kv.first}, kv.second});
                out = semcodec::json_dumps(pyv::Obj(l));
            } else if (op == "rebuild") {
                out = Json::quote(h.rebuild_reply(dict_of(str(a, "fragment")), semcodec::json_loads(str(a, "values")).as<pyv::List>()));
            } else if (op == "site.open") {
                site = h.open_site(host, port); out = "\"good\"";
            } else if (op == "site.call") {
                if (!site) throw std::runtime_error("driver: no site");
                out = site->call(str(a, "engine_op"), str(a, "args"));
            } else if (op == "boundary.open") {
                boundary = h.open_boundary(host, port, (int)a["udp_port"].n); out = "\"good\"";
            } else if (op == "boundary.call") {
                if (!boundary) throw std::runtime_error("driver: no boundary");
                out = boundary->call(str(a, "engine_op"), str(a, "args"));
            } else if (op == "boundary.counts") {
                if (!boundary) throw std::runtime_error("driver: no boundary");
                out = "{\"registers\":" + std::to_string(boundary->registers()) + ",\"requests\":" + std::to_string(boundary->requests()) +
                      ",\"rejected\":" + std::to_string(boundary->rejected()) + "}";
            } else if (op == "boundary.stop") {
                if (!boundary) throw std::runtime_error("driver: no boundary");
                boundary->stop(); boundary.reset(); out = "\"good\"";
            } else if (op == "encode_register") {
                EtrRegisterConfig c; c.key_id = (int)a["key_id"].n; c.alg = (int)a["alg"].n; c.password = str(a, "password");
                c.notify = a["notify"].b; c.merge = true; c.nonce = (uint64_t)a["nonce"].n; c.ttl = (uint32_t)a["ttl"].n;
                c.xtr_id.assign(16, 0x5a); c.site_id.assign(8, 0x01);
                Mapping m; m.prefix = str(a, "prefix"); Rloc rl; rl.address = str(a, "rloc"); rl.priority = 1; rl.weight = 100;
                m.rlocs.push_back(rl);
                out = Json::quote(hex(encode_etr_register4(c, {m})));
            } else {
                throw std::runtime_error("driver: unknown operation " + op);
            }
            std::cout << "{\"ok\":true,\"result\":" << out << "}\n" << std::flush;
        } catch (const std::exception& x) {
            std::cout << "{\"ok\":false,\"error\":" << Json::quote(x.what()) << "}\n" << std::flush;
        }
    }
    if (boundary) boundary->stop();
}
