#!/usr/bin/env python3
from pathlib import Path
import argparse,subprocess,tarfile,json,io,hashlib
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--stage',required=True);a=p.parse_args()
here=Path(__file__).resolve().parents[1];repo=here
for _ in range(5):repo=repo.parent
prefix=str(here.relative_to(repo))+'/'
paths=subprocess.check_output(['git','-C',str(repo),'ls-files',prefix],text=True).splitlines()
paths+=['opt/frognet_semantic/ribbit/'+x for x in ['cpp/README.md','cpp/include/ribbit_pp.hpp','cpp/src/ribbit_pp.cpp','cpp/qualify.sh','cpp/tests/test_capabilities.cpp','cpp/tools/check_capabilities.sh','docs/API-PLUS-PLUS.md','docs/TUPLE-INTERFACE-VALIDATION.md']]
content={};modes={}
for path in paths:
 content[path]=(repo/path).read_bytes();mode=subprocess.check_output(['git','-C',str(repo),'ls-files','-s','--',path],text=True).split()[0];modes[path]=int(mode,8)&0o777
content['_recovery/README.txt']=b'Reconstructed source overlay, not a byte-identical recovery of the lost archive. Extract into the FrogNet repository root. See opt/frognet_semantic/ribbit/examples/senzmatica/RECOVERY.md and README.md. No binaries or dependencies included. Stage and hashes are in SOURCE-CHECKPOINT.json.\n'
backend=repo.parent/'senzmatica-converted'
content[prefix+'backend-complete.patch']=subprocess.check_output(['git','-C',str(backend),'diff','--binary','1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4','HEAD'])
content['_recovery/FROGNET-LICENSE.txt']=(repo/'LICENSE').read_bytes()
content['_recovery/SENZMATICA-LICENSE.txt']=(repo.parent/'senzmatica-core/LICENSE').read_bytes()
meta={'reconstructed':True,'stage':a.stage,'frognet_commit':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),'backend_commit':subprocess.check_output(['git','-C',str(backend),'rev-parse','HEAD'],text=True).strip(),'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in content.items()}}
content['_recovery/SOURCE-CHECKPOINT.json']=(json.dumps(meta,indent=2)+'\n').encode()
a.out.parent.mkdir(parents=True,exist_ok=True)
with tarfile.open(a.out,'w:gz') as tar:
 for name,data in content.items():
  item=tarfile.TarInfo(name);item.size=len(data);item.mode=modes.get(name,0o644);item.mtime=0;tar.addfile(item,io.BytesIO(data))
with tarfile.open(a.out) as tar:
 for name,data in content.items():assert tar.extractfile(name).read()==data
 assert not any(name.endswith(('.o','.so','.pyc','.class')) or '/build/' in name for name in tar.getnames())
print(json.dumps({'out':str(a.out),'files':len(content),'bytes':a.out.stat().st_size,'sha256':hashlib.sha256(a.out.read_bytes()).hexdigest()}))
