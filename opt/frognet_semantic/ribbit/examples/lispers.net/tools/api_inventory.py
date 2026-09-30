#!/usr/bin/env python3
import ast,json,sys
p=sys.argv[1]; tree=ast.parse(open(p).read()); out=[]
for n in tree.body:
 if isinstance(n,ast.ClassDef) and n.name=='api_init':
  for f in n.body:
   if isinstance(f,ast.FunctionDef) and not f.name.startswith('_'):
    args=[]
    for a in f.args.args[1:]: args.append(a.arg)
    out.append({'method':f.name,'args':args,'doc':ast.get_docstring(f) or ''})
print(json.dumps(out,indent=2))
