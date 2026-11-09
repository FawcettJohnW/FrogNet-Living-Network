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
from pathlib import Path
import argparse,subprocess,json,os,hashlib
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--state',type=Path,required=True);a=p.parse_args();w=a.workspace.resolve();state=a.state.resolve();state.mkdir(parents=True,exist_ok=True)
here=Path(__file__).resolve().parents[1];backend=w/'senzmatica-converted';service=backend/'core-lib/src/main/java/com/magma/core/service/RibbitConfigurationService.java';original=service.read_text()
env=os.environ.copy();env['CPATH']=str(w/'build-deps/lz4/usr/include')+':'+str(w/'baseline-tools/cjson/include');env['LIBRARY_PATH']=str(w/'build-deps/lz4/usr/lib/x86_64-linux-gnu')+':'+str(w/'baseline-tools/cjson/lib')
objects=[str(here/'build'/(name+'.o')) for name in ['frogram','ram_host','ribbit_space','lf_ebr','ribbit_pp']]
summary=[]
def build(name):
 with (state/(name+'-build-driver.log')).open('w') as log:subprocess.run(['python',str(here/'tools/maven.py'),'--workspace',str(w),'--source',str(backend),'--state',str(state/(name+'-build'))],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
def device(broken):
 subprocess.run(['g++','-std=c++20','-O2','-pthread','-Wall','-Wextra','-Werror','-Wno-free-nonheap-object','-I'+str(here.parents[1]/'cpp/include'),'-I'+str(here.parent/'mqtt/stock/include'),'-DDEVICE_BROKEN='+str(broken),str(here/'native/device.cpp')]+objects+[str(here.parent/'mqtt/stock/lib/libmosquitto.so.1'),'-llz4','-lcrypto','-o',str(here/'build/test-device')],env=env,check=True)
def run(name,expected):
 with (state/(name+'-driver.log')).open('w') as log:r=subprocess.run(['python',str(here/'tools/run.py'),'--workspace',str(w),'--state',str(state/name),'--mode','ram'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
 result=json.loads((state/name/'result.json').read_text());result['control']=name
 if expected:
  assert r.returncode!=0 and result['status']=='FAIL' and expected in result.get('error',''),(name,result)
  print('PASS mutant caught: '+name,flush=True)
 else:
  assert r.returncode==0 and result['status']=='PASS',result
  print('PASS positive: '+str(len(result['checks']))+' checks',flush=True)
 summary.append(result)
try:
 run('positive-before',None)
 old='return wanted != null && wanted.path("generation").isTextual() &&\n            wanted.path("generation").asText().equals(actual.path("generation").asText());';assert old in original
 service.write_text(original.replace(old,'return wanted != null;',1));build('generation');run('generation','stale completion added history')
 old='.and("deviceParameterConfiguration.updateHistory.messageId").ne(marker)';assert old in original
 service.write_text(original.replace(old,''));build('deduplication');run('deduplication','duplicate completion added history')
 service.write_text(original);build('restored');device(1);run('physical-repeat','duplicate observation repeated physical application')
finally:
 service.write_text(original);device(0);build('final-restored')
run('positive-after',None)
(state/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
print('RESULT PASS 3/3 semantic controls; source/binaries restored',flush=True)
