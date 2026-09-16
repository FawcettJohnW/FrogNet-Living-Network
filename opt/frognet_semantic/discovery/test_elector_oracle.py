#!/usr/bin/env python3
"""Gated wrapper - runs the post-merge databasehost elector proof."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.elector_check import main
if __name__ == "__main__":
    sys.exit(main())
