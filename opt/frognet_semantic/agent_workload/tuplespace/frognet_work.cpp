// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// [P2P_01] A c10d Work whose completion state a Python backend can reach.
//
// The defect, measured against gloo as a differential control:
//
//     backend.recv  is_completed   gloo False   frognet True/False
//     pg.recv       is_completed   gloo False   frognet False
//     pg.recv after wait()         gloo True    frognet False   <-- defect
//     tensor correct               both True
//
// The Python wait() IS called -- traced, three times in the probe -- and the
// operation succeeds. What never happens is the transition of the inherited
// C++ Work state. Work::finish() and Work::finishAndThrow() are PROTECTED
// (Work.hpp:145-152), so a Python subclass can override wait() and
// is_completed() but cannot drive the state those defaults read.
//
// Gloo's RecvWork::wait() does the transport wait and then calls
// finishAndThrow(exception) -- that call is the missing piece, and it is not
// reachable from Python.
//
// So this is the smallest possible native shim: a Work subclass that is
// already finished at construction, and one that finishes when waited on.
// The FrogNet backend does its work in Python and hands back one of these
// as the handle, so the object c10d holds is a real C++ Work with real C++
// completion state.

#include <torch/extension.h>
#include <torch/csrc/distributed/c10d/Work.hpp>

#include <exception>
#include <vector>

namespace {

// DDP's reducer asks a Work for its future -- the base throws
// "Work::getFuture not implemented.", which broke DistributedDataParallelCPU
// after the native Work was wired in even though every collective still
// passed. A backend Work must supply one.
static c10::intrusive_ptr<c10::ivalue::Future> completedFuture(
    const std::vector<at::Tensor>& tensors) {
  auto fut = c10::make_intrusive<c10::ivalue::Future>(
      c10::ListType::create(c10::TensorType::get()));
  fut->markCompleted(c10::IValue(tensors));
  return fut;
}


// Finished the moment it is made. For an operation that has already
// completed by the time the backend returns -- which is every collective in
// this backend today.
class FrogDoneWork : public c10d::Work {
 public:
  explicit FrogDoneWork(std::vector<at::Tensor> result)
      : result_(std::move(result)) {
    // The transition Python cannot perform.
    finish(nullptr);
  }

  bool isCompleted() override { return true; }
  bool isSuccess() const override { return true; }

  bool wait(std::chrono::milliseconds /*timeout*/ = kNoTimeout) override {
    return true;
  }

  std::vector<at::Tensor> result() override { return result_; }

  c10::intrusive_ptr<c10::ivalue::Future> getFuture() override {
    return completedFuture(result_);
  }

 private:
  std::vector<at::Tensor> result_;
};

// Finishes on wait(), which is gloo's observable shape: incomplete until
// asked, complete afterwards. For an operation a backend wants to report as
// genuinely pending.
class FrogWaitWork : public c10d::Work {
 public:
  explicit FrogWaitWork(std::vector<at::Tensor> result)
      : result_(std::move(result)) {}

  bool isCompleted() override { return done_; }
  bool isSuccess() const override { return done_; }

  bool wait(std::chrono::milliseconds /*timeout*/ = kNoTimeout) override {
    if (!done_) {
      done_ = true;
      finishAndThrow(nullptr);
    }
    return true;
  }

  std::vector<at::Tensor> result() override { return result_; }

  c10::intrusive_ptr<c10::ivalue::Future> getFuture() override {
    return completedFuture(result_);
  }

  // [P2P_02] dist.recv(tensor) with no src asks the Work which rank it
  // received from -- distributed_c10d.recv does work._source_rank() and
  // returns it to the caller. A receive-from-anyone is meaningless if the
  // receiver cannot then say who it was.
  int sourceRank() const override {
    if (src_ < 0) {
      TORCH_CHECK(false, "frognet: this Work has no source rank");
    }
    return src_;
  }

  void setSourceRank(int src) { src_ = src; }

 private:
  std::vector<at::Tensor> result_;
  bool done_ = false;
  int src_ = -1;
};

}  // namespace

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.doc() = "FrogNet c10d Work shim -- native completion state [P2P_01]";
  m.def(
      "done_work",
      [](std::vector<at::Tensor> result) {
        return c10::static_intrusive_pointer_cast<c10d::Work>(
            c10::make_intrusive<FrogDoneWork>(std::move(result)));
      },
      "A Work already finished at construction.");
  m.def(
      "wait_work",
      [](std::vector<at::Tensor> result, int src) {
        auto w = c10::make_intrusive<FrogWaitWork>(std::move(result));
        if (src >= 0) {
          w->setSourceRank(src);
        }
        return c10::static_intrusive_pointer_cast<c10d::Work>(w);
      },
      py::arg("result"), py::arg("src") = -1,
      "A Work that finishes when waited on, as gloo's does. `src` is the "
      "rank it received from, for recv_anysource.");
}
