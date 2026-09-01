#!/opt/frognet_semantic/venv/bin/python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# core/database_handler.py
#
# Database-host role handler - carries the databasehost.frognet election CRITERIA,
# living on a handler the same way SotFMediaHandler carries the media criteria.
#
# evaluate(lan_list) is PURE: given the LAN candidate list (already gathered from
# capability memory by the election machinery), return the candidate that best meets
# the database role's requirements. No I/O, no probes, no spawning.
#
# Unlike the format handlers this one has no wire codec - a database host is elected,
# not a content type converged - but it registers in format_registry alongside them
# so role evaluation dispatches through the one registry.


from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class DatabaseRoleHandler(UnRESTHandler):
    """databasehost.frognet criteria. DB is memory-bound: mysql is the hard gate,
    RAM (innodb headroom) dominates the ceiling, cores secondary. Load is NOT a
    factor - the role is a durable assignment, not a live scheduler."""

    ROLE_NAME = "databasehost"
    CANDIDATE_TYPE = "DatabaseCandidate"
    DEFAULT_PORT = 3306

    @property
    def mode(self):
        return "databasehost"

    @staticmethod
    def _ip_to_int(ip):
        try:
            a, b, c, d = (int(x) for x in ip.split("."))
            return (a << 24) | (b << 16) | (c << 8) | d
        except Exception:
            return 0

    # [DBHOST_STATIC_RANK_V1] Below this the candidate is ineligible, full stop.
    DISK_FREE_FLOOR_GB = 1.0

    def score(self, cand):
        """Database-host fitness from STATIC signal. mysql present AND RUNNING is
        the hard GATE (installed-but-not-serving is not a DB host). Beyond the gate:
          - RAM       - INSTALLED memory (mem_total_kb), the dominant factor,
                       plus credit for a configured innodb pool
                       (mysql_innodb_pool_bytes). Never free/available RAM:
                       that moves, and mem_available_kb is a fallback used
                       only when mem_total_kb was omitted.
          - disk        - disk_class, a property of the media. NOT measured
                       throughput or fsync latency: both move with load, and
                       [DBHOST_STATIC_RANK_V1] forbids that. See the comment
                       at the disk term below for the feedback loop it caused.
          - capacity - disk_free_gb; out-of-space is fatal.
          - CPU       - cores x cpu_mhz. NOT cpu_bench_total: that is re-run by
                       the advertiser and carries the same load objection as
                       fsync.
        Load is NOT scored, and neither is anything that MOVES with load.
        [DBHOST_STATIC_RANK_V1]: every term below is a property of the hardware
        or of its configuration -- installed RAM, innodb pool, disk class, cores,
        clock. Nothing here is re-measured by the advertiser, so a candidate
        scores identically write-to-write, every reader agrees, and holding the
        role cannot change the score that won it. Free space is a GATE only.
        Pure read of the candidate dict."""
        import math
        # GATE: must be serving, not merely installed. Fall back to 'mysql' only if
        # the running flag was never published (older sensor) so we don't over-reject.
        running = cand.get("mysql_running")
        if running is None:
            running = cand.get("mysql")
        if not running:
            return -1.0

        cores = max(1, int(cand.get("cores", 1)))

        # RAM: prefer available; fall back to total. Credit configured innodb pool.
        mem_avail_gb = int(cand.get("mem_available_kb", 0)) / (1024 * 1024)
        mem_total_gb = int(cand.get("mem_total_kb", 0)) / (1024 * 1024)
        # [DBHOST_STATIC_RANK_V1] installed RAM, never free. The role is a durable
        # assignment, so a transient free-RAM reading must not move it - same reason
        # load is excluded below. A node's tuple then ranks identically write-to-write
        # and every box agrees. Fall back to available only if mem_total was omitted.
        mem_gb = mem_total_gb if mem_total_gb > 0 else mem_avail_gb
        innodb_gb = int(cand.get("mysql_innodb_pool_bytes", 0)) / (1024 ** 3)
        ram_score = mem_gb * 1.0 + innodb_gb * 0.5      # tuned pool is a real plus

        # DISK: the MEDIA, not this second's throughput.
        #
        # [DBHOST_STATIC_RANK_V1] extended to disk. It fixed RAM for exactly this
        # reason -- "a transient free-RAM reading must not move it... a node's
        # tuple then ranks identically write-to-write and every box agrees" --
        # and left disk measured. disk_write_mbps and disk_fsync_ms are re-taken
        # by the 60s advertiser and move with LOAD, and [DBHOST_NO_LOAD_V1] says
        # in as many words that load must not select this role. fsync latency is
        # loadavg wearing a hardware name: 2ms idle, 50ms busy, and the clamp
        # below turned that into a 3.3x swing in the dominant disk term.
        #
        # Which produced a feedback loop, not a lottery: the elected host takes
        # every node's writes, its fsync degrades, its score drops, it loses the
        # role, the new winner inherits the load and degrades in turn. Measured
        # 2026-08-08, Seattle5 alone: 10.250.250.1 -> 10.160.160.1 ->
        # 10.177.177.1 -> 10.130.130.1 -> 10.250.250.1 -> 10.130.130.1 ->
        # 10.160.160.1, seven changes in one hour, /etc/hosts holding a steady 24
        # names throughout. Winning the role destroyed the qualification for it.
        #
        # disk_class is a property of the hardware. It does not move when the box
        # gets busy, so every reader scores the same candidate identically and
        # keeps scoring it identically while it holds the role.
        klass = (cand.get("disk_class") or "unknown").lower()
        disk_perf = 8.0 * {"nvme": 1.6, "ssd": 1.3, "hdd": 1.0,
                           "sdcard": 0.4, "unknown": 0.9}.get(klass, 0.9)

        # capacity: a GATE, not a multiplier.
        #
        # [DBHOST_STATIC_RANK_V1] cap_factor was a step function -- 0.1 / 0.5 /
        # 1.0 at 1 GB and 5 GB -- so a node sitting near either line had its
        # ENTIRE score halved or doubled by a few megabytes of ordinary disk
        # churn, and flipped back on the next advertise. A cliff in a continuous
        # input is a coin toss dressed as arithmetic.
        #
        # Out of space is genuinely fatal and stays fatal: below the floor the
        # candidate is ineligible outright, which is a gate like mysql_running
        # above and not a thumb on the scale. Above it, free space does not rank
        # anyone -- a box with 40 GB free is not a better database host than one
        # with 20.
        disk_free = float(cand.get("disk_free_gb", 0.0))
        if disk_free < self.DISK_FREE_FLOOR_GB:
            return -1.0
        cap_factor = 1.0

        # CPU: the silicon, not a benchmark taken under load.
        #
        # [DBHOST_STATIC_RANK_V1] cpu_bench_total is re-run by the advertiser and
        # a benchmark on a busy box measures the busy, not the box -- the same
        # objection as fsync, and the same feedback loop. cores and cpu_mhz are
        # properties of the machine and do not move.
        cpu_score = cores * int(cand.get("cpu_mhz", 1000)) / 1000.0 * 0.5

        ceiling = (ram_score + disk_perf + cpu_score) * cap_factor

        # [DBHOST_NO_LOAD_V1] load is NOT a databasehost selection factor - the
        # role is a durable assignment, not a moment-to-moment scheduler, so a
        # transient loadavg must not move it. RAM/disk/CPU/free-space only.
        return ceiling

    def evaluate(self, hosts_list, lan_list):
        """Database host is elected over the ENTIRE (WAN-inclusive) hosts_list, NOT the
        LAN list: the DB is the pond's authority, so the best box wins pond-wide -
        including remote sites reached over the WireGuard overlay (dev wgx) - not just
        this LAN. lan_list is accepted for signature uniformity and ignored here.
        Pure: score each, drop ineligible, pick best, tiebreak by highest IP."""
        best = None
        for cand in hosts_list or []:
            ip = cand.get("lan_ip", "")
            if not ip:
                continue
            try:
                sc = self.score(cand)
            except Exception:
                # [EVAL_ISOLATE_V1] a malformed candidate blob (a dict where a
                # numeric field is expected) must not abort the whole role's
                # election and trip the fallback. Drop THIS candidate, keep the
                # rest, so the legitimate winner is still elected pond-wide.
                continue
            if sc < 0:
                continue
            key = (sc, self._ip_to_int(ip))
            if best is None or key > best[0]:
                best = (key, cand)
        return best[1] if best else None
