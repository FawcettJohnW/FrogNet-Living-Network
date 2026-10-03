# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""frogcomms -- the FrogNet Communicator in Python: the second implementation of COMMUNICATOR-SPEC.md / FNAV-SPEC.md.
It shares nothing with the C++ implementation but the contract: the same tuples in comms-ram, the same fnav wire to
the same media server, the same rules. The memory is reached through the platform's client library (libcomms_tuples,
a C ABI over ribbit::TuplesClient), as every Ribbit Python participant reaches it; everything above that is Python."""
