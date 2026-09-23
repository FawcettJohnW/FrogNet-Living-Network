// A persistent hash array-mapped trie: the unordered index. Lookup walks log32(n) nodes; a write
// copies those nodes (arrays of at most 32 slots) and returns a new root. Immutable, so the memory
// can publish a root with one atomic store and readers never see a torn tree.
#pragma once
#include <memory>
#include <vector>
#include <functional>
#include <cstdint>
#include <string>

template <class K, class V>
class HMap {
public:
    struct Node;
    using NP = std::shared_ptr<const Node>;
    struct Leaf { K key; V val; };
    using LP = std::shared_ptr<const Leaf>;
    struct Slot { LP leaf; NP child; };               // child set => branch; else leaf. Copying a slot copies two pointers.
    struct Node {
        uint32_t bitmap = 0;
        std::vector<Slot> slots;                       // one per set bit, in bit order
        std::vector<LP> collisions;                    // only at the bottom level
    };
    static LP mkleaf(const K& k, const V& v) { auto l = std::make_shared<Leaf>(); l->key = k; l->val = v; return l; }
    static const int BITS = 4, MAXDEPTH = 60;

    static uint64_t hash(const K& k) { return std::hash<K>{}(k) * 0x9E3779B97F4A7C15ULL; }
    static int index(uint32_t bitmap, uint32_t bit) { return __builtin_popcount(bitmap & (bit - 1)); }

    static const V* find(const NP& root, const K& k) {
        const Node* n = root.get(); if (!n) return nullptr;
        uint64_t h = hash(k);
        for (int shift = 0; n; shift += BITS) {
            if (shift >= MAXDEPTH) { for (auto& p : n->collisions) if (p->key == k) return &p->val; return nullptr; }
            uint32_t bit = 1u << ((h >> shift) & 15);
            if (!(n->bitmap & bit)) return nullptr;
            const Slot& s = n->slots[index(n->bitmap, bit)];
            if (s.child) { n = s.child.get(); continue; }
            return s.leaf->key == k ? &s.leaf->val : nullptr;
        }
        return nullptr;
    }

    static NP insert(const NP& root, const K& k, const V& v, bool* existed = nullptr) {
        if (existed) *existed = false;
        return ins(root, k, v, hash(k), 0, existed);
    }
    static NP erase(const NP& root, const K& k, bool* found = nullptr) {
        if (found) *found = false;
        if (!root) return root;
        NP r = del(root, k, hash(k), 0, found);
        if (r && r->bitmap == 0 && r->collisions.empty()) return nullptr;
        return r;
    }
    static void each(const NP& root, const std::function<bool(const K&, const V&)>& f) { bool go = true; walk(root, f, go); }
    static size_t count(const NP& root) { size_t n = 0; each(root, [&](const K&, const V&) { n++; return true; }); return n; }

private:
    static NP ins(const NP& n, const K& k, const V& v, uint64_t h, int shift, bool* existed) {
        auto out = std::make_shared<Node>();
        if (shift >= MAXDEPTH) {
            if (n) { out->collisions = n->collisions; }
            for (auto& p : out->collisions) if (p->key == k) { p = mkleaf(k, v); if (existed) *existed = true; return out; }
            out->collisions.push_back(mkleaf(k, v)); return out;
        }
        uint32_t bit = 1u << ((h >> shift) & 15);
        if (!n) { out->bitmap = bit; out->slots.push_back(Slot{mkleaf(k, v), nullptr}); return out; }
        *out = *n;
        int i = index(n->bitmap, bit);
        if (!(n->bitmap & bit)) { out->bitmap |= bit; out->slots.insert(out->slots.begin() + i, Slot{mkleaf(k, v), nullptr}); return out; }
        Slot& s = out->slots[i];
        if (s.child) { s.child = ins(s.child, k, v, h, shift + BITS, existed); return out; }
        if (s.leaf->key == k) { s.leaf = mkleaf(k, v); if (existed) *existed = true; return out; }
        // two leaves in one slot: push both down
        NP sub = ins(nullptr, s.leaf->key, s.leaf->val, hash(s.leaf->key), shift + BITS, nullptr);
        sub = ins(sub, k, v, h, shift + BITS, nullptr);
        s.child = sub; s.leaf.reset();
        return out;
    }
    static NP del(const NP& n, const K& k, uint64_t h, int shift, bool* found) {
        if (shift >= MAXDEPTH) {
            auto out = std::make_shared<Node>(*n);
            for (auto it = out->collisions.begin(); it != out->collisions.end(); ++it) if ((*it)->key == k) { out->collisions.erase(it); if (found) *found = true; break; }
            return out;
        }
        uint32_t bit = 1u << ((h >> shift) & 15);
        if (!(n->bitmap & bit)) return n;
        int i = index(n->bitmap, bit);
        const Slot& s = n->slots[i];
        auto out = std::make_shared<Node>(*n);
        if (s.child) {
            NP c = del(s.child, k, h, shift + BITS, found);
            if (c == s.child) return n;
            if (!c || (c->bitmap == 0 && c->collisions.empty())) { out->bitmap &= ~bit; out->slots.erase(out->slots.begin() + i); }
            else if (c->collisions.empty() && __builtin_popcount(c->bitmap) == 1 && !c->slots[0].child) { out->slots[i] = c->slots[0]; }   // collapse a single leaf upward
            else out->slots[i].child = c;
            return out;
        }
        if (s.leaf->key != k) return n;
        if (found) *found = true;
        out->bitmap &= ~bit; out->slots.erase(out->slots.begin() + i);
        return out;
    }
    static void walk(const NP& n, const std::function<bool(const K&, const V&)>& f, bool& go) {
        if (!n || !go) return;
        for (auto& s : n->slots) { if (s.child) walk(s.child, f, go); else go = f(s.leaf->key, s.leaf->val); if (!go) return; }
        for (auto& p : n->collisions) { go = f(p->key, p->val); if (!go) return; }
    }
};
