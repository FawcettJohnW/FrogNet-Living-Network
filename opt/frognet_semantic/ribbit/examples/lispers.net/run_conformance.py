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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
import argparse, json, os, sys, unittest
from pathlib import Path

def main():
 p=argparse.ArgumentParser(); p.add_argument('--adapter',choices=['control','command'],required=True)
 # lispers.net's API port: 8800 (8080 is Apache on a FrogNet host). Start lispers.net with ./RESTART-LISP 8800.
 p.add_argument('--source'); p.add_argument('--host'); p.add_argument('--user'); p.add_argument('--password',default=None); p.add_argument('--port',type=int,default=8800); p.add_argument('--http',action='store_true')
 p.add_argument('--command'); p.add_argument('--capabilities',default='')
 # [PER_OPERATION_TIMING_V1] every adapter call is timed; --timings writes the table as JSON (tools/compare_timings.py
 # puts two of them side by side); --repeat runs the whole suite N times in one process for more samples
 p.add_argument('--timings',default=None); p.add_argument('--label',default=None); p.add_argument('--repeat',type=int,default=1)
 a=p.parse_args(); sys.path.insert(0,str(Path(__file__).parent))
 if a.adapter=='control':
  from conformance.adapters.control import ControlAdapter
  if not all([a.source,a.host,a.user]) or a.password is None: p.error("control requires --source --host --user --password (--password '' for lispers.net's default root account)")
  inner=ControlAdapter(a.source,a.host,a.user,a.password,a.port,not a.http)
  label=a.label or 'lispers.net control API %s:%d' % (a.host,a.port)
 else:
  import shlex; from conformance.adapters.command import CommandAdapter
  inner=CommandAdapter(shlex.split(a.command),set(filter(None,a.capabilities.split(','))))
  label=a.label or 'command: %s' % a.command
 from conformance.timing import TimedAdapter
 adapter=TimedAdapter(inner,label)
 from tests.test_read_contract import ReadContract, TransportStatsContract
 from tests.test_roundtrip_contract import RoundTripContract
 from tests.test_mapping_contract import MappingContract, DatabaseMappingContract
 from tests.test_registration_contract import RegistrationContract
 from tests.test_wire_contract import WireContract
 from tests.test_site_authorization import SiteAuthorizationContract
 from tests.test_ddt_contract import DDTContract
 ReadContract.adapter=adapter; TransportStatsContract.adapter=adapter; RoundTripContract.adapter=adapter; MappingContract.adapter=adapter; DatabaseMappingContract.adapter=adapter; RegistrationContract.adapter=adapter; WireContract.adapter=adapter; SiteAuthorizationContract.adapter=adapter; DDTContract.adapter=adapter
 build=lambda:unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(ReadContract),unittest.defaultTestLoader.loadTestsFromTestCase(TransportStatsContract),unittest.defaultTestLoader.loadTestsFromTestCase(RoundTripContract),unittest.defaultTestLoader.loadTestsFromTestCase(MappingContract),unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseMappingContract),unittest.defaultTestLoader.loadTestsFromTestCase(RegistrationContract),unittest.defaultTestLoader.loadTestsFromTestCase(WireContract),unittest.defaultTestLoader.loadTestsFromTestCase(SiteAuthorizationContract),unittest.defaultTestLoader.loadTestsFromTestCase(DDTContract)])
 ok=True; t0=__import__('time').time()
 for i in range(max(1,a.repeat)):
  r=unittest.TextTestRunner(verbosity=2).run(build()); ok=ok and r.wasSuccessful()   # a fresh suite each pass: unittest drops tests once run
 adapter.print_table(sys.stdout)
 if a.timings: adapter.write(a.timings,{'adapter':a.adapter,'repeat':a.repeat,'wall_s':round(__import__('time').time()-t0,3),'suite_ok':ok})
 return 0 if ok else 1
if __name__=='__main__': raise SystemExit(main())
