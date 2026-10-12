#!/usr/bin/env python3
"""[REFUSAL_SAYS_WHY_V1] a refused call carries the region's `why` in the exception text, so a client can print the reason
(e.g. which live session holds a mapping) instead of the bare error code. Usage: apply_refused_why.py <frogram.cpp>"""
import sys
p = sys.argv[1]; s = open(p).read()
old = '''throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + obj["error"].s);'''
new = '''throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + obj["error"].s + (obj["why"].s.empty() ? std::string() : " (" + obj["why"].s + ")"));   // [REFUSAL_SAYS_WHY_V1]'''
if new not in s:
    assert old in s, "pattern not found"
    s = s.replace(old, new); open(p, "w").write(s)
print("ok")
