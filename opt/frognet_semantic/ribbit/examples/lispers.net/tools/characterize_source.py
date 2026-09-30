#!/usr/bin/env python3
import json,re,sys
from pathlib import Path
root=Path(sys.argv[1]); files=[]
for d in ('lisp','apps'):
 files.extend((root/d).rglob('*.py'))
patterns={'lock_acquire':r'\.acquire\s*\(','lock_release':r'\.release\s*\(','json_dumps':r'json\.dumps\s*\(','json_loads':r'json\.loads\s*\(','requests_http':r'requests\.(?:get|put|delete|post)\s*\(','curl_mentions':r'\bcurl\b','sleep_calls':r'time\.sleep\s*\(','ipc_calls':r'\blisp_ipc\s*\(','ipc_lock_refs':r'lisp_ipc_lock','latency_debug':r'lisp_latency_debug\s*\('}
out={'python_files':len(files)}
for k,p in patterns.items(): out[k]=sum(len(re.findall(p,f.read_text(errors='ignore'))) for f in files)
print(json.dumps(out,indent=2,sort_keys=True))
