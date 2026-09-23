// A lock-free ordered map: a skip list with marked next-pointers (Herlihy & Shavit, LockFreeSkipList).
// Insert and remove are CAS-based; a failed CAS is retried, never waited on. Lookups and ordered walks
// take nothing. Removed nodes are unlinked, then retired through EBR. Keys and values are immutable once
// a node is in the list; the value is a pointer-sized thing the caller owns.
#pragma once
#include "ebr.hpp"
#include <atomic>
#include <cstdint>
#include <random>
#include <functional>
struct MemStats; MemStats& memstats();
void memstats_mapnode(int delta); void memstats_map(int delta);

template <class K, class V, int L = 20>
class LFMap {
public:
    static const int MAXLEVEL = L;
    // A node carries only as many next-pointers as its level: most nodes are level 0 and cost one pointer,
    // not MAXLEVEL+1 of them. Allocated with room for next[top+1]; freed through free_node.
    struct Node {
        K key; V val; int top;
        std::atomic<uintptr_t> next[1];
        static Node* make(const K& k, const V& v, int t) {
            void* mem = ::operator new(sizeof(Node) + (size_t)t * sizeof(std::atomic<uintptr_t>));
            Node* n = new (mem) Node(k, v, t);
            for (int i = 0; i <= t; i++) new (&n->next[i]) std::atomic<uintptr_t>(0);
            memstats_mapnode(1);
            return n;
        }
        static void free_node(void* p) { Node* n = (Node*)p; n->~Node(); ::operator delete(p); memstats_mapnode(-1); }
    private:
        Node(const K& k, const V& v, int t) : key(k), val(v), top(t) {}
    };
    LFMap() : head_(Node::make(K(), V(), MAXLEVEL)) { memstats_map(1); }
    ~LFMap() { Node* p = head_; while (p) { Node* n = ptr(p->next[0].load()); Node::free_node(p); p = n; } memstats_map(-1); }

    // insert; false if the key was there (existing node returned via out)
    bool add(const K& key, const V& val, Node** out = nullptr) {
        int top = rand_level();
        Node* preds[MAXLEVEL + 1]; Node* succs[MAXLEVEL + 1];
        for (;;) {
            if (find(key, preds, succs)) { if (out) *out = succs[0]; return false; }
            Node* n = Node::make(key, val, top);
            for (int lv = 0; lv <= top; lv++) n->next[lv].store((uintptr_t)succs[lv]);
            uintptr_t exp = (uintptr_t)succs[0];
            if (!preds[0]->next[0].compare_exchange_strong(exp, (uintptr_t)n)) { Node::free_node(n); continue; }
            for (int lv = 1; lv <= top; lv++) {
                for (;;) {
                    uintptr_t e = (uintptr_t)succs[lv];
                    if (preds[lv]->next[lv].compare_exchange_strong(e, (uintptr_t)n)) break;
                    find(key, preds, succs);
                    n->next[lv].store((uintptr_t)succs[lv]);
                }
            }
            size_.fetch_add(1, std::memory_order_relaxed);
            if (out) *out = n;
            return true;
        }
    }
    // remove; returns the node's value through out, false if absent
    bool remove(const K& key, V* out = nullptr) {
        Node* preds[MAXLEVEL + 1]; Node* succs[MAXLEVEL + 1];
        if (!find(key, preds, succs)) return false;
        Node* n = succs[0];
        for (int lv = n->top; lv >= 1; lv--) {
            uintptr_t s = n->next[lv].load();
            while (!marked(s)) { n->next[lv].compare_exchange_weak(s, s | 1); }
        }
        uintptr_t s = n->next[0].load();
        for (;;) {
            if (marked(s)) return false;                         // someone else removed it
            if (n->next[0].compare_exchange_strong(s, s | 1)) {
                find(key, preds, succs);                         // physically unlink
                size_.fetch_sub(1, std::memory_order_relaxed);
                if (out) *out = n->val;
                ebr::retire((void*)n, &Node::free_node);
                return true;
            }
        }
    }
    Node* get(const K& key) const {
        Node* pred = head_; Node* curr = nullptr;
        for (int lv = MAXLEVEL; lv >= 0; lv--) {
            curr = ptr(pred->next[lv].load(std::memory_order_acquire));
            while (curr) {
                uintptr_t s = curr->next[lv].load(std::memory_order_acquire);
                while (marked(s)) { curr = ptr(s); if (!curr) break; s = curr->next[lv].load(std::memory_order_acquire); }
                if (!curr || !(curr->key < key)) break;
                pred = curr; curr = ptr(s);
            }
        }
        return (curr && !(key < curr->key) && !(curr->key < key)) ? curr : nullptr;
    }
    // first live node with key >= k (null if none)
    Node* lower_bound(const K& k) const {
        Node* pred = head_; Node* curr = nullptr;
        for (int lv = MAXLEVEL; lv >= 0; lv--) {
            curr = ptr(pred->next[lv].load(std::memory_order_acquire));
            while (curr) {
                uintptr_t s = curr->next[lv].load(std::memory_order_acquire);
                while (marked(s)) { curr = ptr(s); if (!curr) break; s = curr->next[lv].load(std::memory_order_acquire); }
                if (!curr || !(curr->key < k)) break;
                pred = curr; curr = ptr(s);
            }
        }
        return curr;
    }
    Node* first() const { return live(ptr(head_->next[0].load(std::memory_order_acquire))); }
    Node* after(Node* n) const { return live(ptr(n->next[0].load(std::memory_order_acquire))); }
    size_t size() const { long s = size_.load(std::memory_order_relaxed); return s < 0 ? 0 : (size_t)s; }
    // in-order walk from the first key >= lo; f returns false to stop
    void walk(const K& lo, const std::function<bool(Node*)>& f) const { for (Node* n = lower_bound(lo); n; n = after(n)) if (!f(n)) return; }
    void walk_all(const std::function<bool(Node*)>& f) const { for (Node* n = first(); n; n = after(n)) if (!f(n)) return; }
    // number of live nodes with key < k (a walk: O(n))
    size_t rank(const K& k) const { size_t r = 0; for (Node* n = first(); n && n->key < k; n = after(n)) r++; return r; }
    Node* nth(size_t i) const { Node* n = first(); while (n && i) { n = after(n); i--; } return n; }
    Node* last() const { Node* l = nullptr; for (Node* n = first(); n; n = after(n)) l = n; return l; }

private:
    Node* head_;
    std::atomic<long> size_{0};
    static Node* ptr(uintptr_t p) { return (Node*)(p & ~(uintptr_t)1); }
    static bool marked(uintptr_t p) { return p & 1; }
    Node* live(Node* n) const { while (n && marked(n->next[0].load(std::memory_order_acquire))) n = ptr(n->next[0].load(std::memory_order_acquire)); return n; }
    static int rand_level() { static thread_local std::mt19937 rng{std::random_device{}()}; int l = 0; while (l < MAXLEVEL && (rng() & 1)) l++; return l; }
    // find with physical removal of marked nodes; true if key present (succs[0] is it)
    bool find(const K& key, Node** preds, Node** succs) {
    retry:
        Node* pred = head_;
        for (int lv = MAXLEVEL; lv >= 0; lv--) {
            Node* curr = ptr(pred->next[lv].load(std::memory_order_acquire));
            for (;;) {
                if (!curr) break;
                uintptr_t s = curr->next[lv].load(std::memory_order_acquire);
                while (marked(s)) {                           // snip out a marked node
                    uintptr_t e = (uintptr_t)curr;
                    if (!pred->next[lv].compare_exchange_strong(e, (uintptr_t)ptr(s))) goto retry;
                    curr = ptr(s); if (!curr) break; s = curr->next[lv].load(std::memory_order_acquire);
                }
                if (!curr || !(curr->key < key)) break;
                pred = curr; curr = ptr(s);
            }
            preds[lv] = pred; succs[lv] = curr;
        }
        Node* c = succs[0];
        return c && !(key < c->key) && !(c->key < key);
    }
};
