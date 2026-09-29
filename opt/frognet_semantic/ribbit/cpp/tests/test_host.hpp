// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_host.hpp -- a RAM host for the platform's own tests. Include it AFTER ../src/ram_host.cpp: the tests embed the
// host in their own process and call its api() directly, so they compare every answer over the wire with the host's.
#pragma once
struct TestRam : ribbit::RamHost {
    std::string api;
    explicit TestRam(std::string endpoint) : api(std::move(endpoint)) {}
    std::string vendor_id() const override { return "test"; }
    std::string vendor_version() const override { return "test"; }
    std::string api_path() const override { return api; }
};
// api() without a listener: make h the host this file's own api() consults
inline void attach(TestRam& h) { HOST = &h; API_PATH = h.api_path(); REGION_SERVICE = h.region_service(); }
