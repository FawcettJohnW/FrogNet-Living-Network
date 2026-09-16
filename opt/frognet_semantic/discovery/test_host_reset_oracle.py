#!/usr/bin/env python3
"""Gated wrapper - runs the hostReset reconcile proof across the real modules."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.host_reset_check import main
if __name__ == "__main__":
    sys.exit(main())
