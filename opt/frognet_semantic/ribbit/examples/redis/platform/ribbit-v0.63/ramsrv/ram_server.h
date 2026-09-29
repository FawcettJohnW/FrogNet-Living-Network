// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <string>
// Start the in-process C++ FrogNet RAM server. 0 = listening (accept loop is detached), else errno.
int ram_server_start(const std::string &listen_on, bool quiet);
