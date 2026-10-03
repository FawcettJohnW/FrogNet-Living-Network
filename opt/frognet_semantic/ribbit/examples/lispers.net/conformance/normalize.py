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
import json

def canonical(value):
    """Normalize representation only; never invent semantic equivalence."""
    if isinstance(value, str):
        s=value.strip()
        if s and s[0] in '[{':
            try: value=json.loads(s)
            except json.JSONDecodeError: return value
    if isinstance(value, dict): return {k: canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list): return [canonical(x) for x in value]
    return value

def rloc_addresses(value):
    value=canonical(value)
    if not isinstance(value,dict): return []
    rows=value.get('rlocs',value.get('rloc_set',[]))
    if not isinstance(rows,list): return []
    out=[]
    for row in rows:
        if isinstance(row,str): out.append(row)
        elif isinstance(row,dict) and isinstance(row.get('address'),str): out.append(row['address'])
    return out

def mapping_prefix(value):
    value=canonical(value)
    return value.get('prefix') if isinstance(value,dict) else None
