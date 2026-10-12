"""apply_engine_once.py FNWP_ENGINE_HPP -- [PRIMED_LEARNED_ONCE_V1] [TEMPLATE_BY_POINTER_V1]
 * The primed store is LEARNED ONCE per process, not once per engine. Every session made an engine and every engine
   learned all the exemplars again (detail::learn: parse, infer, build fragments) -- a rinaperf client made two sessions,
   the host one per rlite-ctl call: 12% of a short client's instructions. Now the first engine for an API learns them and
   later engines copy the learned templates (keys computed once too). An engine for another API learns its own.
 * A request holds a POINTER to its template, not a copy. prepare copied the whole Template (fragments, field lists,
   skeleton) into every request's context (6% of a client); the template lives in the engine's published snapshot,
   which is never freed while the engine lives, so the pointer stays good for the request's life.
Applied at build after apply_primed_templates.py, apply_template_precompute.py and apply_client_known_shape.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''        for (const auto& e : detail::primed()) {         // [PRIMED_TEMPLATES_V1] learned here, not on the wire
            uint64_t op = 0; auto tp = detail::learn(api_, e.m, e.p, e.b, e.a, op);
            if (tp) tpl_[{e.m, semtpl::canonical_semantic_key(e.m, e.p, e.b)}] = *tp;
            else fprintf(stderr, "[PRIMED_TEMPLATES_V1] client: an exemplar no longer learns (%s %s)\\n", e.m.c_str(), e.p.c_str()); }
        if (!detail::primed().empty()) publish(); }''',
'''        // [PRIMED_LEARNED_ONCE_V1] learned once per process (for the first API asked), copied into every later engine
        auto learn_all = [](const std::string& api) { std::vector<std::pair<Key, Template>> v;
            for (const auto& e : detail::primed()) { uint64_t op = 0; auto tp = detail::learn(api, e.m, e.p, e.b, e.a, op);
                if (tp) v.emplace_back(Key{e.m, semtpl::canonical_semantic_key(e.m, e.p, e.b)}, *tp);
                else fprintf(stderr, "[PRIMED_TEMPLATES_V1] client: an exemplar no longer learns (%s %s)\\n", e.m.c_str(), e.p.c_str()); }
            return v; };
        static const std::string first_api = api_;
        static const std::vector<std::pair<Key, Template>> learned = learn_all(first_api);
        if (api_ == first_api) { for (const auto& kv : learned) tpl_[kv.first] = kv.second; }
        else { for (auto& kv : learn_all(api_)) tpl_[kv.first] = std::move(kv.second); }
        if (!detail::primed().empty()) publish(); }''')
rep('''        for (const auto& x : detail::primed()) {         // [PRIMED_TEMPLATES_V1] the same exemplars the client learns
            uint64_t op = 0; auto tp = detail::learn(api_, x.m, x.p, x.b, x.a, op);
            if (tp) tpl_[op] = *tp; else fprintf(stderr, "[PRIMED_TEMPLATES_V1] server: an exemplar no longer learns (%s %s)\\n", x.m.c_str(), x.p.c_str()); }
        if (!detail::primed().empty()) publish_templates(); }''',
'''        // [PRIMED_LEARNED_ONCE_V1] learned once per process (for the first API asked), copied into every later engine
        auto learn_all = [](const std::string& api) { std::vector<std::pair<uint64_t, Template>> v;
            for (const auto& x : detail::primed()) { uint64_t op = 0; auto tp = detail::learn(api, x.m, x.p, x.b, x.a, op);
                if (tp) v.emplace_back(op, *tp); else fprintf(stderr, "[PRIMED_TEMPLATES_V1] server: an exemplar no longer learns (%s %s)\\n", x.m.c_str(), x.p.c_str()); }
            return v; };
        static const std::string first_api = api_;
        static const std::vector<std::pair<uint64_t, Template>> learned = learn_all(first_api);
        if (api_ == first_api) { for (const auto& kv : learned) tpl_[kv.first] = kv.second; }
        else { for (auto& kv : learn_all(api_)) tpl_[kv.first] = std::move(kv.second); }
        if (!detail::primed().empty()) publish_templates(); }''')
# the request's template by pointer
rep('''        std::string key; uint64_t op = 0; detail::IKey ik; Template t;''',
    '''        std::string key; uint64_t op = 0; detail::IKey ik; const Template* tp = nullptr;   // [TEMPLATE_BY_POINTER_V1] into the snapshot''')
rep('''    c->t = it->second; c->op = semtpl::opcode_for(c->key);''', '''    c->tp = &it->second; c->op = semtpl::opcode_for(c->key);''')
s = s.replace('c->t.', 'c->tp->').replace('const Template& t = c->t;', 'const Template& t = *c->tp;')
open(p, 'w').write(s); print("engine once applied")
