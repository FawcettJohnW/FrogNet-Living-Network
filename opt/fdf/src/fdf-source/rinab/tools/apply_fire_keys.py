"""apply_fire_keys.py RAM_MEMORY_HPP -- [FIRE_KEYS_ONCE_V1] a row's six pattern keys are made once, when the row is
made, not on every write.

Memory::fire(Row*) built six pattern-key strings (allocations) and hashed each, on EVERY write, to find the held reads
waiting on patterns that cover the row. A row's address is set once, so its keys and their hashes never change: they
are computed when the row is created and kept in it. Same lookups, same wakes. Callgrind (rr, whole host):
Memory::fire was 4.8% of the host. Applied at build; the vendored v0.63 source stays untouched."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''    Row* next_in_variable = nullptr;              // set once, before publication
};''', '''    Row* next_in_variable = nullptr;              // set once, before publication
    std::string pkey[6]; size_t phash[6] = {0, 0, 0, 0, 0, 0};   // [FIRE_KEYS_ONCE_V1] set once, before publication
};''')
rep('''            [&] { Row* r = new Row; r->service = s; r->variable = v; r->instance = i; return r; },''',
    '''            [&] { Row* r = new Row; r->service = s; r->variable = v; r->instance = i;
                  const std::string k6[6] = {pattern_key(s, "", ""), pattern_key("", v, ""), pattern_key("", "", i),
                                             pattern_key(s, v, ""), pattern_key(s, "", i), pattern_key("", v, i)};
                  for (int j = 0; j < 6; ++j) { r->pkey[j] = k6[j]; r->phash[j] = std::hash<std::string>()(k6[j]); }   // [FIRE_KEYS_ONCE_V1]
                  return r; },''')
rep('''        const std::string& s = r->service; const std::string& v = r->variable; const std::string& i = r->instance;
        const std::string keys[6] = {pattern_key(s, "", ""), pattern_key("", v, ""), pattern_key("", "", i),
                                     pattern_key(s, v, ""), pattern_key(s, "", i), pattern_key("", v, i)};
        for (const auto& k : keys)
            if (Pattern* p = patterns_.find(std::hash<std::string>()(k), [&](Pattern* n) { return n->key == k; })) p->triggers.fire();''',
    '''        for (int j = 0; j < 6; ++j) { const std::string& k = r->pkey[j];   // [FIRE_KEYS_ONCE_V1] made with the row
            if (Pattern* p = patterns_.find(r->phash[j], [&](Pattern* n) { return n->key == k; })) p->triggers.fire(); }''')
open(p, 'w').write(s)
print("fire keys applied")
