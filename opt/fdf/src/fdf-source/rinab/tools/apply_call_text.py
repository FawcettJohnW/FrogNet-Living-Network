"""apply_call_text.py FROGRAM_HPP FROGRAM_CPP -- [CALL_TEXT_V1] frogram::Session::call_text: the call, with the answer
handed back as its text (status checked), not parsed into Json. For a region whose answer is not JSON -- the RINA
region's wait answer is lines of events ([WAIT_ANSWER_IS_TEXT_V1]): the host's codec then carries it as one raw field
(no JSON tree is built for it at the host, for extraction or for the reply proof), and the library reads the lines
itself. Same request path, same coalescing, same waits; only the last step differs. Applied at build."""
import sys
hp, cp = sys.argv[1], sys.argv[2]
h = open(hp).read(); c = open(cp).read()
a = '''    Json call(const std::string& method, const std::string& path, const std::string& body = "",
              double timeout_s = 15.0);'''
assert h.count(a) == 1
h = h.replace(a, a + '''
    // [CALL_TEXT_V1] the same call; the answer comes back as its text (an HTTP status >= 400 still throws Refused)
    std::string call_text(const std::string& method, const std::string& path, const std::string& body = "",
                          double timeout_s = 15.0);''')
a = '''    if (!p->err.empty()) throw Unreachable(p->err);
    Json obj = Json::parse(p->answer.body);'''
assert c.count(a) == 1
c = c.replace(a, '''    if (!p->err.empty()) throw Unreachable(p->err);
    if (tl_text_out) { if (p->answer.status >= 400) throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + p->answer.body);
                       *tl_text_out = p->answer.body; return Json(); }   // [CALL_TEXT_V1]
    Json obj = Json::parse(p->answer.body);''')
a = '''Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {'''
assert c.count(a) == 1
c = c.replace(a, '''static thread_local std::string* tl_text_out = nullptr;   // [CALL_TEXT_V1] set by call_text around call()
std::string Session::call_text(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    std::string out; tl_text_out = &out;
    try { call(method, path, body, timeout_s); } catch (...) { tl_text_out = nullptr; throw; }
    tl_text_out = nullptr; return out;
}
''' + a)
open(hp, 'w').write(h); open(cp, 'w').write(c); print("call text applied")
