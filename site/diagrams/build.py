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
"""
build.py — regenerate every FrogNet site diagram.

    cd diagrams && python3 build.py

Writes SVGs into ../assets/diagrams/. Pure standard library, no dependencies.
Each diagram also runs standalone (python3 ladder_vs_cliff.py).
"""
import ladder_vs_cliff
import split_and_merge
import same_diff_full
import broker_boundary
from frognet_svg import write

DIAGRAMS = [
    ("ladder-vs-cliff.svg", ladder_vs_cliff.build),
    ("split-and-merge.svg", split_and_merge.build),
    ("same-diff-full.svg", same_diff_full.build),
    ("broker-boundary.svg", broker_boundary.build),
]


def main():
    for name, fn in DIAGRAMS:
        write(name, fn())
    print(f"\n{len(DIAGRAMS)} diagrams written to ../assets/diagrams/")


if __name__ == "__main__":
    main()
