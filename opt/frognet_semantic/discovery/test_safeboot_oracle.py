#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.safeboot_check import main
if __name__ == "__main__":
    sys.exit(main())
