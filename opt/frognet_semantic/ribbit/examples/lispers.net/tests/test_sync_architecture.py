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
from pathlib import Path
import unittest


class SynchronizationArchitectureTests(unittest.TestCase):
    def test_ribbit_participant_has_no_mutex_or_condition_variable(self):
        src = (Path(__file__).parents[1] / 'ribbit_cpp' / 'ribbit_lisp.cpp').read_text()
        forbidden = ('std::mutex', 'std::condition_variable', 'std::lock_guard', 'std::unique_lock')
        found = [token for token in forbidden if token in src]
        self.assertEqual(found, [], 'participant-local synchronization survived: ' + ', '.join(found))

    def test_held_views_publish_runtime_lock_free_snapshots(self):
        root = Path(__file__).parents[1]
        src = (root / 'ribbit_cpp' / 'ribbit_lisp.cpp').read_text()
        self.assertNotIn('shared_ptr<const MappingTable>', src)
        self.assertNotIn('std::atomic_load(&', src)
        self.assertIn('std::atomic<const MappingTable*>', src)
        self.assertIn('ebr.guard()', src)
        self.assertIn('ebr.retire(old)', src)

    def test_convergence_waits_are_memory_truths(self):
        src = (Path(__file__).parents[1] / 'ribbit_cpp' / 'ribbit_lisp.cpp').read_text()
        self.assertIn('resolver-applied|', src)
        self.assertIn('map-cache-applied|', src)
        self.assertIn('map-resolver-applied', src)
        self.assertIn('ram->read("lisp",variable,"",(int64_t)after,5.0)', src)
        self.assertIn('ram->write_nowait("lisp",variable,instance', src)


if __name__ == '__main__':
    unittest.main()
