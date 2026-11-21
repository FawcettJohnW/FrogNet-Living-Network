#!/usr/bin/env python3
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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
statevector.py -- [ROLES_EXERCISE_V1] Compute's circuit simulator: a state vector of up to eight qubits.

Written here, for this purpose, with nothing imported but the standard library (the design's R28). It runs the
OpenQASM 3 programs the exercise writes: qubit[n] q; bit[m] c; the gates h, x, rx, ry, rz, cx, ccx; one measure.

The result enters the exercise record, so it has to be the same on every machine. The amplitudes are floating point;
the probability that the measured qubit is 1 is rounded to parts in a million, and the shots are drawn against that
whole number from a generator seeded with the SHA-256 of the step and the program text. No floating-point value
leaves this file.
"""
import cmath
import hashlib
import math
import re

MAX_QUBITS = 8
SHOTS = 1024
_STMT = re.compile(r"^\s*(?:(\w+)\s*\[\s*(\d+)\s*\]\s*=\s*measure\s+(\w+)\s*\[\s*(\d+)\s*\]|(\w+)\s*(?:\(([^)]*)\))?\s*(.*))\s*$")


class CircuitError(ValueError):
    pass


def _one(state, n, q, m):
    """Apply the 2x2 matrix m to qubit q."""
    bit = 1 << q
    for i in range(1 << n):
        if not i & bit:
            a, b = state[i], state[i | bit]
            state[i], state[i | bit] = m[0][0] * a + m[0][1] * b, m[1][0] * a + m[1][1] * b


def _flip(state, n, controls, target):
    mask = sum(1 << c for c in controls)
    bit = 1 << target
    for i in range(1 << n):
        if i & mask == mask and not i & bit:
            state[i], state[i | bit] = state[i | bit], state[i]


def probability_ppm(program):
    """The probability, in parts in a million, that the program's measured qubit reads 1."""
    n = None
    state = None
    measured = None
    for raw in program.split(";"):
        s = raw.strip()
        if not s or s.startswith("OPENQASM") or s.startswith("include"):
            continue
        m = _STMT.match(s)
        if not m:
            raise CircuitError("a statement this simulator does not read: %r" % s)
        if m.group(1):
            measured = int(m.group(4))
            continue
        gate, arg, rest = m.group(5), m.group(6), m.group(7)
        if gate == "qubit" or gate == "bit":
            size = re.match(r"^\[\s*(\d+)\s*\]\s*\w+$", (rest or "").strip()) or re.match(r"^\s*$", "x")
            k = re.search(r"\[\s*(\d+)\s*\]", s)
            if gate == "qubit":
                n = int(k.group(1)) if k else 1
                if not 1 <= n <= MAX_QUBITS:
                    raise CircuitError("%d qubits: this simulator runs 1 to %d" % (n, MAX_QUBITS))
                state = [0j] * (1 << n)
                state[0] = 1 + 0j
            continue
        if state is None:
            raise CircuitError("a gate before any qubit is declared: %r" % s)
        qs = [int(x) for x in re.findall(r"\[\s*(\d+)\s*\]", rest or "")]
        if any(q >= n for q in qs):
            raise CircuitError("a qubit this circuit does not have: %r" % s)
        if gate in ("rx", "ry", "rz"):
            t = float(arg)
            c, sn = math.cos(t / 2), math.sin(t / 2)
            mat = {"rx": ((c, -1j * sn), (-1j * sn, c)), "ry": ((c, -sn), (sn, c)),
                   "rz": ((cmath.exp(-0.5j * t), 0), (0, cmath.exp(0.5j * t)))}[gate]
            _one(state, n, qs[0], mat)
        elif gate == "h":
            r = 1 / math.sqrt(2)
            _one(state, n, qs[0], ((r, r), (r, -r)))
        elif gate == "x":
            _flip(state, n, [], qs[0])
        elif gate == "cx":
            _flip(state, n, qs[:1], qs[1])
        elif gate == "ccx":
            _flip(state, n, qs[:2], qs[2])
        else:
            raise CircuitError("the gate %r is not one this simulator has (h, x, rx, ry, rz, cx, ccx)" % gate)
    if state is None or measured is None:
        raise CircuitError("the program declares no qubits or measures none")
    p = sum(abs(a) ** 2 for i, a in enumerate(state) if i >> measured & 1)
    return max(0, min(1000000, int(round(p * 1000000))))


def run(step, program, shots=SHOTS):
    """(shots, ones): the counts for this program at this step. The same on every machine."""
    ppm = probability_ppm(program)
    seed = hashlib.sha256(("%d\n" % step).encode() + program.encode()).digest()
    ones, k, block = 0, 0, b""
    for _ in range(shots):
        if len(block) < 4:
            block += hashlib.sha256(seed + k.to_bytes(4, "big")).digest()
            k += 1
        draw, block = int.from_bytes(block[:4], "big") % 1000000, block[4:]
        if draw < ppm:
            ones += 1
    return shots, ones
