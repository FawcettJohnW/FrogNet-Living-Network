// A persistent ordered map: nodes are immutable, an update copies the path from the root and
// returns a new root; every old root stays valid and consistent for whoever still holds it.
// This is what lets the memory publish a new state with one atomic store and let readers walk
// a tree that nothing will ever mutate. AVL-balanced; each node carries its subtree size so
// rank/offset queries are O(log n).
#pragma once
#include <memory>
#include <functional>
#include <vector>
#include <cstdint>

template <class K, class V>
class PMap {
public:
    struct Node {
        K key; V val;
        std::shared_ptr<const Node> l, r;
        int h; size_t n;
    };
    using NP = std::shared_ptr<const Node>;

    static int height(const NP& p) { return p ? p->h : 0; }
    static size_t size(const NP& p) { return p ? p->n : 0; }

    static NP mk(const K& k, const V& v, NP l, NP r) {
        auto p = std::make_shared<Node>();
        p->key = k; p->val = v; p->h = 1 + std::max(height(l), height(r)); p->n = 1 + size(l) + size(r);
        p->l = std::move(l); p->r = std::move(r);
        return p;
    }
    static NP rotl(const NP& p) { return mk(p->r->key, p->r->val, mk(p->key, p->val, p->l, p->r->l), p->r->r); }
    static NP rotr(const NP& p) { return mk(p->l->key, p->l->val, p->l->l, mk(p->key, p->val, p->l->r, p->r)); }
    static NP balance(const K& k, const V& v, NP l, NP r) {
        int hl = height(l), hr = height(r);
        if (hl > hr + 1) {
            if (height(l->l) >= height(l->r)) return rotr(mk(k, v, l, r));
            return rotr(mk(k, v, rotl(l), r));
        }
        if (hr > hl + 1) {
            if (height(r->r) >= height(r->l)) return rotl(mk(k, v, l, r));
            return rotl(mk(k, v, l, rotr(r)));
        }
        return mk(k, v, std::move(l), std::move(r));
    }

    static const V* find(const NP& p, const K& k) {
        const Node* c = p.get();
        while (c) { if (k < c->key) c = c->l.get(); else if (c->key < k) c = c->r.get(); else return &c->val; }
        return nullptr;
    }
    // insert or replace; `existed` reports whether the key was present
    static NP insert(const NP& p, const K& k, const V& v, bool* existed = nullptr) {
        if (!p) { if (existed) *existed = false; return mk(k, v, nullptr, nullptr); }
        if (k < p->key) return balance(p->key, p->val, insert(p->l, k, v, existed), p->r);
        if (p->key < k) return balance(p->key, p->val, p->l, insert(p->r, k, v, existed));
        if (existed) *existed = true;
        return mk(k, v, p->l, p->r);
    }
    static NP min_node(NP p) { while (p && p->l) p = p->l; return p; }
    static NP max_node(NP p) { while (p && p->r) p = p->r; return p; }
    static NP erase_min(const NP& p) { if (!p->l) return p->r; return balance(p->key, p->val, erase_min(p->l), p->r); }
    static NP erase(const NP& p, const K& k, bool* found = nullptr) {
        if (!p) { if (found) *found = false; return p; }
        if (k < p->key) return balance(p->key, p->val, erase(p->l, k, found), p->r);
        if (p->key < k) return balance(p->key, p->val, p->l, erase(p->r, k, found));
        if (found) *found = true;
        if (!p->l) return p->r;
        if (!p->r) return p->l;
        NP m = min_node(p->r);
        return balance(m->key, m->val, p->l, erase_min(p->r));
    }
    // in-order visit of keys in [lo, hi] (hi ignored when hi_unbounded); f returns false to stop
    static void range(const NP& p, const K& lo, const K& hi, bool hi_unbounded, const std::function<bool(const K&, const V&)>& f) {
        std::vector<const Node*> st; const Node* c = p.get();
        while (c) { if (c->key < lo) c = c->r.get(); else { st.push_back(c); c = c->l.get(); } }
        while (!st.empty()) {
            const Node* x = st.back(); st.pop_back();
            if (!hi_unbounded && hi < x->key) return;
            if (!f(x->key, x->val)) return;
            c = x->r.get();
            while (c) { if (c->key < lo) c = c->r.get(); else { st.push_back(c); c = c->l.get(); } }
        }
    }
    // reverse in-order visit of keys in [lo, hi]
    static void rrange(const NP& p, const K& lo, const K& hi, bool hi_unbounded, const std::function<bool(const K&, const V&)>& f) {
        std::vector<const Node*> st; const Node* c = p.get();
        auto push_right = [&](const Node* c2) { while (c2) { if (!hi_unbounded && hi < c2->key) c2 = c2->l.get(); else { st.push_back(c2); c2 = c2->r.get(); } } };
        push_right(c);
        while (!st.empty()) {
            const Node* x = st.back(); st.pop_back();
            if (x->key < lo) return;
            if (!f(x->key, x->val)) return;
            push_right(x->l.get());
        }
    }
    static void each(const NP& p, const std::function<bool(const K&, const V&)>& f) {
        if (!p) return;
        std::vector<const Node*> st; const Node* c = p.get();
        while (c) { st.push_back(c); c = c->l.get(); }
        while (!st.empty()) { const Node* x = st.back(); st.pop_back(); if (!f(x->key, x->val)) return; c = x->r.get(); while (c) { st.push_back(c); c = c->l.get(); } }
    }
    // the i-th element in order (0-based); null if out of range
    static const Node* nth(const NP& p, size_t i) {
        const Node* c = p.get();
        while (c) { size_t ls = size(c->l); if (i < ls) c = c->l.get(); else if (i == ls) return c; else { i -= ls + 1; c = c->r.get(); } }
        return nullptr;
    }
    // number of keys strictly less than k
    static size_t rank(const NP& p, const K& k) {
        size_t r = 0; const Node* c = p.get();
        while (c) { if (k < c->key) c = c->l.get(); else if (c->key < k) { r += size(c->l) + 1; c = c->r.get(); } else return r + size(c->l); }
        return r;
    }
};
