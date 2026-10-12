# Retired experiments (2026-10-07 restart)

These were built for the earlier plan and pass or failed against it. They are kept for their evidence, not run.

They rest on what the restart removed:

- member-to-member data;
- routes and the route-change re-send;
- broadcast LAN joining and the member's offer;
- host-change neighbour propagation;
- the NAT relay;
- the topology rig.

What replaces them is in the plan document ("FD&F — plan and tests (restart, 2026-10-07)"), tests T1–T7, and the
checks in tools/: discovery-check, selfreg-check, mapwait-check, move-check, and transport-conformance
(build/bin/transport-conformance). RESULTS.md records why each retired.
