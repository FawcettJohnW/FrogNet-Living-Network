"""[GEN_V1] oracle: bump is +1, named, locked, announced as the `gen` fact; world is the sum over alive members only."""
import json, os, sys, tempfile
d = tempfile.mkdtemp()
os.environ["FROGNET_GEN"] = d + "/gen"; os.environ["FROGNET_WORLD_GEN"] = d + "/world"; os.environ["FDF_FACTS"] = d + "/facts.json"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from internet_tunnels_v3 import gen, region
said = []
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)
check("1 starts at 0", gen.read() == 0)
check("2 bump is +1 and returns it", gen.bump("routes_changed", "applier", log=said.append) == 1 and gen.read() == 1)
check("3 GEN line names why and by", said[-1] == "GEN: 0 -> 1 why=routes_changed by=applier")
check("4 announced as the gen fact", region.read_facts().get("gen") == 1 and region.read_facts().get("gen_why") == "routes_changed")
try:
    gen.bump("because", "x"); check("5 unknown reason refused", False)
except AssertionError:
    check("5 unknown reason refused", True)
gen.bump("member_came", "applier"); gen.bump("service_host_changed", "elect")
check("6 three bumps = 3", gen.read() == 3)
parts = [{"name": "A", "alive": True, "facts": {"gen": 3}}, {"name": "B", "alive": True, "facts": {"gen": 7}},
         {"name": "C", "alive": False, "facts": {"gen": 100}}, {"name": "D", "alive": True, "facts": {}}]
w = gen.world(parts)
check("7 world = sum of ALIVE members, missing gen = 0", w["world"] == 10 and w["members"] == {"A": 3, "B": 7, "D": 0})
gen.write_world(w); check("8 world file round-trips", gen.read_world()["world"] == 10)
sys.exit(0 if ok else 1)
