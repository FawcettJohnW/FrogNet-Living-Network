// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frognet_link.cpp -- the byte-moving half of a tensor-plane link, in C++.
// [NATIVE_LINK_V1] John 2026-11-12 (#3 of the unused-mechanisms list).
//
// tensor_plane.py decides WHAT moves (held reads on the family, which cell
// fired, where a landed cell goes). This file moves the bytes, on the same
// wire, with no interpreter in the loop:
//
//   Sender    owner side of one link. One writer thread per channel set; each
//             takes the next record of the cells in flight (round-robin, the
//             header first), sizes it to 20 ms of that set's measured rate,
//             and writes it with one writev straight from the tensor's memory.
//   Receiver  reader side. One landing thread per set; a header names the
//             cell, the registered destination (expect) is looked up, and data
//             records are received straight into it -- or into a buffer the
//             landing hands to Python as a tensor, without a copy.
//
// Completion goes back to Python on one notifier thread per object, which is
// the only thread here that ever takes the GIL.
//
// The wire is tensor_plane.py's, byte for byte, so either end can be Python:
//     [kind:1][id:4 BE][total:4 BE][offset:4 BE][len:4 BE][body]
//     kind 'H' body = [name_len:2 BE][name][json header]    kind 'D' body = bytes
#include <torch/extension.h>
#include <pybind11/pybind11.h>

#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace py = pybind11;
using clk = std::chrono::steady_clock;

static double now_s() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}

static void put32(uint8_t* p, uint32_t v) { v = htonl(v); std::memcpy(p, &v, 4); }
static uint32_t get32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return ntohl(v); }

static void write_all(int fd, struct iovec* iov, int n) {
    while (n > 0) {
        ssize_t k = ::writev(fd, iov, n);
        if (k < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("writev: ") + std::strerror(errno));
        }
        while (n > 0 && size_t(k) >= iov[0].iov_len) { k -= iov[0].iov_len; ++iov; --n; }
        if (n > 0) { iov[0].iov_base = static_cast<char*>(iov[0].iov_base) + k; iov[0].iov_len -= k; }
    }
}

static void read_all(int fd, void* dst, size_t n) {
    char* p = static_cast<char*>(dst);
    while (n > 0) {
        ssize_t k = ::recv(fd, p, n, 0);
        if (k == 0) throw std::runtime_error("peer closed");
        if (k < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("recv: ") + std::strerror(errno));
        }
        p += k; n -= size_t(k);
    }
}

// --------------------------------------------------------------------------------------------- sender
struct Delivery {
    uint32_t id = 0;
    std::string head;          // H body: [name_len][name][json]
    const uint8_t* data = nullptr;
    uint64_t n = 0, next = 0, done = 0;
    bool hdr_sent = false;
    py::object keep;           // the tensor / buffer, alive until every byte is in a kernel
    double t0 = 0, seg_max = 0;
    uint32_t sets = 0, recs = 0;
    size_t alongside = 0;
};

class Sender {
public:
    Sender(py::function on_done, double ceil_s, uint64_t seg_min, uint64_t seg_max)
        : on_done_(std::move(on_done)), ceil_s_(ceil_s), seg_min_(seg_min), seg_max_(seg_max) {
        std::thread([this] { notifier(); }).detach();
    }

    void add_set(int fd) {
        int si;
        { std::lock_guard<std::mutex> g(mu_); si = int(fds_.size()); fds_.push_back(fd); rate_.push_back(0); }
        std::thread([this, si, fd] { writer(si, fd); }).detach();
    }

    // Called with the GIL held.
    void push(uint32_t id, py::bytes head, uint64_t ptr, uint64_t n, py::object keep) {
        auto d = std::make_shared<Delivery>();
        d->id = id; d->head = std::string(head); d->data = reinterpret_cast<const uint8_t*>(ptr);
        d->n = n; d->keep = std::move(keep);
        { std::lock_guard<std::mutex> g(mu_); d->alongside = active_.size(); active_.push_back(d); }
        cv_.notify_all();
    }

    void stop() {
        stopped_ = true;
        std::vector<int> fds;
        { std::lock_guard<std::mutex> g(mu_); fds = fds_; }
        for (int fd : fds) ::shutdown(fd, SHUT_RDWR);
        cv_.notify_all(); dcv_.notify_all();
    }

    py::dict rates() {
        py::dict out;
        std::lock_guard<std::mutex> g(mu_);
        for (size_t i = 0; i < rate_.size(); ++i) out[py::int_(i)] = rate_[i];
        return out;
    }

private:
    uint64_t seg(int si) {
        double r = rate_[si];
        if (r <= 0) return 64 * 1024;
        double s = r * ceil_s_;
        if (s < double(seg_min_)) s = double(seg_min_);
        if (s > double(seg_max_)) s = double(seg_max_);
        return uint64_t(s);
    }

    void writer(int si, int fd) {
        uint8_t rec[17];
        try {
            while (!stopped_) {
                std::shared_ptr<Delivery> d; char kind = 0; uint64_t off = 0, ln = 0;
                {
                    std::unique_lock<std::mutex> g(mu_);
                    for (;;) {
                        if (stopped_) return;
                        size_t m = active_.size();
                        for (size_t k = 0; k < m && !d; ++k) {
                            auto& c = active_[(rr_ + k) % m];
                            if (!c->hdr_sent) { c->hdr_sent = true; c->t0 = now_s(); d = c; kind = 'H'; rr_ = (rr_ + k + 1) % m; }
                            else if (c->next < c->n) {
                                off = c->next; ln = std::min<uint64_t>(seg(si), c->n - off); c->next = off + ln;
                                d = c; kind = 'D'; rr_ = (rr_ + k + 1) % m;
                            }
                        }
                        if (d) break;
                        cv_.wait_for(g, std::chrono::seconds(1));
                    }
                }
                rec[0] = uint8_t(kind); put32(rec + 1, d->id); put32(rec + 5, uint32_t(d->n));
                if (kind == 'H') {
                    put32(rec + 9, 0); put32(rec + 13, uint32_t(d->head.size()));
                    struct iovec iov[2] = {{rec, 17}, {const_cast<char*>(d->head.data()), d->head.size()}};
                    write_all(fd, iov, 2);
                    if (d->n == 0) finish(d);
                    continue;
                }
                put32(rec + 9, uint32_t(off)); put32(rec + 13, uint32_t(ln));
                struct iovec iov[2] = {{rec, 17}, {const_cast<uint8_t*>(d->data + off), size_t(ln)}};
                auto t = clk::now();
                write_all(fd, iov, 2);
                double dt = std::chrono::duration<double>(clk::now() - t).count();
                bool last;
                {
                    std::lock_guard<std::mutex> g(mu_);
                    double inst = double(ln) / std::max(dt, 1e-6);
                    rate_[si] = rate_[si] <= 0 ? inst : 0.8 * rate_[si] + 0.2 * inst;
                    d->seg_max = std::max(d->seg_max, dt); d->sets |= (1u << si); d->recs++;
                    d->done += ln; last = d->done >= d->n;
                }
                if (last) finish(d);
            }
        } catch (const std::exception& e) {
            std::lock_guard<std::mutex> g(dmu_);
            if (err_.empty()) err_ = "set " + std::to_string(si) + ": " + e.what();
            stopped_ = true; dcv_.notify_all(); cv_.notify_all();
        }
    }

    void finish(const std::shared_ptr<Delivery>& d) {
        {
            std::lock_guard<std::mutex> g(mu_);
            for (auto it = active_.begin(); it != active_.end(); ++it) if (*it == d) { active_.erase(it); break; }
        }
        std::lock_guard<std::mutex> g(dmu_);
        done_.push_back({d, now_s()});
        dcv_.notify_one();
    }

    void notifier() {
        for (;;) {
            std::deque<std::pair<std::shared_ptr<Delivery>, double>> batch; std::string err;
            {
                std::unique_lock<std::mutex> g(dmu_);
                dcv_.wait(g, [this] { return !done_.empty() || stopped_; });
                batch.swap(done_); err = err_;
                if (batch.empty() && stopped_ && err_reported_) return;
                if (!err.empty()) err_reported_ = true;
            }
            if (Py_IsFinalizing()) return;
            py::gil_scoped_acquire gil;
            for (auto& [d, t1] : batch) {
                py::dict st;
                st["t0"] = d->t0; st["t1"] = t1; st["seg_max"] = d->seg_max; st["recs"] = d->recs;
                st["sets"] = d->sets; st["alongside"] = d->alongside;
                try { on_done_(d->id, st, py::none()); } catch (py::error_already_set& e) { e.restore(); PyErr_Print(); }
                d->keep = py::object();
            }
            if (!err.empty()) {
                try { on_done_(0, py::dict(), py::str(err)); } catch (py::error_already_set& e) { e.restore(); PyErr_Print(); }
                if (stopped_) return;
            }
        }
    }

    py::function on_done_;
    double ceil_s_; uint64_t seg_min_, seg_max_;
    std::mutex mu_; std::condition_variable cv_;
    std::vector<int> fds_; std::vector<double> rate_;
    std::deque<std::shared_ptr<Delivery>> active_; size_t rr_ = 0;
    std::mutex dmu_; std::condition_variable dcv_;
    std::deque<std::pair<std::shared_ptr<Delivery>, double>> done_;
    std::string err_; bool err_reported_ = false;
    std::atomic<bool> stopped_{false};
};

// --------------------------------------------------------------------------------------------- receiver
struct Landing {
    std::string name, json;
    uint8_t* dst = nullptr;
    uint64_t n = 0, got = 0;
    bool direct = false;
    int64_t bad = -1;
    std::shared_ptr<std::vector<uint8_t>> buf;
    double t_first = 0, t_land = 0;
    uint32_t sets = 0, recs = 0;
};

class Receiver {
public:
    explicit Receiver(py::function on_land) : on_land_(std::move(on_land)) {
        std::thread([this] { notifier(); }).detach();
    }

    void add_set(int fd) {
        int si;
        { std::lock_guard<std::mutex> g(mu_); si = int(fds_.size()); fds_.push_back(fd); }
        std::thread([this, si, fd] { reader(si, fd); }).detach();
    }

    // Where `name` goes, if it has not arrived yet. Called with the GIL; the
    // caller keeps `keep` (the destination tensor) alive until it is landed.
    void expect(const std::string& name, uint64_t ptr, uint64_t n) {
        std::lock_guard<std::mutex> g(mu_);
        dst_[name] = {reinterpret_cast<uint8_t*>(ptr), n};
    }

    void forget(const std::string& name) { std::lock_guard<std::mutex> g(mu_); dst_.erase(name); }

    void stop() {
        stopped_ = true;
        std::vector<int> fds;
        { std::lock_guard<std::mutex> g(mu_); fds = fds_; }
        for (int fd : fds) ::shutdown(fd, SHUT_RDWR);
        cv_.notify_all(); lcv_.notify_all();
    }

private:
    void reader(int si, int fd) {
        uint8_t rec[17];
        try {
            for (;;) {
                read_all(fd, rec, 17);
                char kind = char(rec[0]);
                uint32_t id = get32(rec + 1), total = get32(rec + 5), off = get32(rec + 9), ln = get32(rec + 13);
                if (kind == 'H') {
                    std::string body(ln, '\0');
                    read_all(fd, body.data(), ln);
                    if (ln < 2) throw std::runtime_error("header too short");
                    uint16_t nl = uint16_t((uint8_t(body[0]) << 8) | uint8_t(body[1]));
                    auto L = std::make_shared<Landing>();
                    L->name = body.substr(2, nl); L->json = body.substr(2 + nl); L->n = total;
                    {
                        std::lock_guard<std::mutex> g(mu_);
                        auto it = dst_.find(L->name);
                        if (it != dst_.end() && it->second.second == total) { L->dst = it->second.first; L->direct = true; dst_.erase(it); }
                        else {
                            if (it != dst_.end()) { L->bad = int64_t(it->second.second); dst_.erase(it); }
                            L->buf = std::make_shared<std::vector<uint8_t>>(total);
                            L->dst = L->buf->data();
                        }
                        open_[id] = L;
                    }
                    cv_.notify_all();
                    if (total == 0) land(id, L);
                    continue;
                }
                std::shared_ptr<Landing> L;
                {
                    std::unique_lock<std::mutex> g(mu_);
                    // A data record can arrive on one flow before its header has
                    // arrived on another; the header is on its way.
                    cv_.wait(g, [&] { return open_.count(id) || stopped_.load(); });
                    if (stopped_ && !open_.count(id)) return;
                    L = open_[id];
                }
                if (total != L->n || uint64_t(off) + ln > L->n)
                    throw std::runtime_error("record id=" + std::to_string(id) + " off=" + std::to_string(off) +
                                             " len=" + std::to_string(ln) + " does not fit " + L->name);
                read_all(fd, L->dst + off, ln);
                bool last;
                {
                    std::lock_guard<std::mutex> g(mu_);
                    if (L->t_first == 0) L->t_first = now_s();
                    L->got += ln; L->recs++; L->sets |= (1u << si);
                    last = L->got >= L->n;
                }
                if (last) land(id, L);
            }
        } catch (const std::exception& e) {
            std::lock_guard<std::mutex> g(lmu_);
            if (err_.empty()) err_ = "set " + std::to_string(si) + ": " + e.what();
            stopped_ = true; lcv_.notify_all(); cv_.notify_all();
            std::lock_guard<std::mutex> g2(mu_);
            for (int f : fds_) ::shutdown(f, SHUT_RDWR);
        }
    }

    void land(uint32_t id, const std::shared_ptr<Landing>& L) {
        L->t_land = now_s();
        { std::lock_guard<std::mutex> g(mu_); open_.erase(id); }
        std::lock_guard<std::mutex> g(lmu_);
        landed_.push_back(L); lcv_.notify_one();
    }

    void notifier() {
        for (;;) {
            std::deque<std::shared_ptr<Landing>> batch; std::string err;
            {
                std::unique_lock<std::mutex> g(lmu_);
                lcv_.wait(g, [this] { return !landed_.empty() || stopped_; });
                batch.swap(landed_); err = err_;
                if (batch.empty() && stopped_ && err_reported_) return;
                if (!err.empty()) err_reported_ = true;
            }
            if (Py_IsFinalizing()) return;
            py::gil_scoped_acquire gil;
            for (auto& L : batch) {
                py::object buf = py::none();
                if (!L->direct) {
                    auto keep = L->buf;   // the tensor owns the vector; no copy
                    buf = py::cast(torch::from_blob(keep->data(), {int64_t(L->n)},
                                                    [keep](void*) mutable { keep.reset(); },
                                                    torch::TensorOptions().dtype(torch::kUInt8)));
                }
                py::dict st;
                st["direct"] = L->direct; st["bad"] = L->bad; st["t_first"] = L->t_first; st["t_land"] = L->t_land;
                st["recs"] = L->recs; st["sets"] = L->sets; st["n"] = L->n;
                try { on_land_(py::str(L->name), py::bytes(L->json), st, buf, py::none()); }
                catch (py::error_already_set& e) { e.restore(); PyErr_Print(); }
            }
            if (!err.empty()) {
                try { on_land_(py::str(""), py::bytes(""), py::dict(), py::none(), py::str(err)); }
                catch (py::error_already_set& e) { e.restore(); PyErr_Print(); }
                return;
            }
        }
    }

    py::function on_land_;
    std::mutex mu_; std::condition_variable cv_;
    std::vector<int> fds_;
    std::unordered_map<std::string, std::pair<uint8_t*, uint64_t>> dst_;
    std::unordered_map<uint32_t, std::shared_ptr<Landing>> open_;
    std::mutex lmu_; std::condition_variable lcv_;
    std::deque<std::shared_ptr<Landing>> landed_;
    std::string err_; bool err_reported_ = false;
    std::atomic<bool> stopped_{false};
};

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.attr("BUILD") = "frognet_link 20261112-r1 [NATIVE_LINK_V1]";
    py::class_<Sender, std::shared_ptr<Sender>>(m, "Sender")
        .def(py::init<py::function, double, uint64_t, uint64_t>())
        .def("add_set", &Sender::add_set)
        .def("push", &Sender::push)
        .def("stop", &Sender::stop, py::call_guard<py::gil_scoped_release>())
        .def("rates", &Sender::rates);
    py::class_<Receiver, std::shared_ptr<Receiver>>(m, "Receiver")
        .def(py::init<py::function>())
        .def("add_set", &Receiver::add_set)
        .def("expect", &Receiver::expect)
        .def("forget", &Receiver::forget)
        .def("stop", &Receiver::stop, py::call_guard<py::gil_scoped_release>());
}
