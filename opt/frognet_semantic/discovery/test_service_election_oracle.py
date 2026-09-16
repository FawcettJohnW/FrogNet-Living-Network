#!/usr/bin/env python3
"""Gated wrapper - runs the service-host-selection proof (control + data + media +
off-mesh install_databasehost candidates + dnsmasq HUP) as part of the discovery
regression suite, so the paradigm stays proven on every gate run."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.service_election_check import main
if __name__ == "__main__":
    sys.exit(main())
