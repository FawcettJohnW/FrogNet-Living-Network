/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// The ONE definition of this package's version. Every program prints it (--version, and its startup line); the RAM
// host answers it (op=version); tools/qualify.sh reads it from here and prints it in a banner at the top of every run,
// with the version of the RAM host it is talking to.
#pragma once
#define RIBBIT_LISP_VERSION "v0.67-region-compat"
// [MIXED_BUILDS_REFUSED_V1] The RAM host versions whose LISP region this front speaks: the region's code and its
// answers are the same in each. Add a version here when a release leaves lisper-ram's region untouched; start the list
// over when one changes it. v0.66 and v0.67 changed only the front (registers in flight, this check) and a diagnostic
// line in ram_host.cpp, so a v0.65 RAM host needs no reinstall.
#define RIBBIT_LISP_REGION_COMPAT {"v0.65-held-by-the-session", "v0.66-registers-in-flight", RIBBIT_LISP_VERSION}
// The LISP region's endpoint on its RAM host (lisper-ram). The vendor names it; every LISP program uses it.
#define LISPER_API "/lisper-api"
