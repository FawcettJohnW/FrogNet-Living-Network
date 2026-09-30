# v0.28 RED — participant synchronization architecture

Base: v0.27 negative-reply green.

A non-vacuous architecture test now requires `ribbit_cpp/ribbit_lisp.cpp` to contain no participant-local C++ mutex/condition-variable synchronization (`std::mutex`, `std::condition_variable`, `std::lock_guard`, `std::unique_lock`).

The test is RED against the unmodified v0.27 implementation, proving that conventional shared-mutable held views and private convergence waits remain.

This checkpoint changes tests/docs only. Runtime behavior is unchanged.
