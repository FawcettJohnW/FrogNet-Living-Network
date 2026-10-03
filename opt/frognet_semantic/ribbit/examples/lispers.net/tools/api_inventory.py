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
