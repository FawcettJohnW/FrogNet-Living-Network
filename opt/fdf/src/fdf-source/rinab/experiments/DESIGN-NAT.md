# DESIGN-NAT: LISP NAT traversal in FD&F Mode B (build item 3, last piece)

**The plan's item:** "LISP NAT traversal (an RTR as the RLOC behind NAT)."

## The rulings it must satisfy

- Connections are up only.
- Liveness is the session.
- No timers, heartbeats or re-asks.
- No control messages over UDP.
- Roles are elected deterministically on measured capability, with identity only as the tie-breaker.

## LISP's mechanism and why it is not copied

lispers.net (L10 in the inventory):

- **The binding.** An xTR behind NAT sends an Info-Request every 15 s (`LISP_INFO_INTERVAL`). The Info-Reply tells it
  its NAT-translated address and port. The periodic message also keeps the NAT's binding open.
- **The registration.** The xTR registers an RTR as its RLOC. Others encapsulate to the RTR, which re-encapsulates
  through the binding.

Two parts conflict with the rulings: the 15 s timer, and UDP control.

## FD&F's mechanism

1. **Who is behind NAT is measured, not configured.** A machine's capability probe asks the mapping region what
   address its session arrives from. If that differs from every address the machine holds, the machine is behind
   NAT. It states this in its own record: `nat=<seen address>`.
2. **The RTR is a role, elected like the map-resolver.** A machine that is directly reachable (no `nat`) and passes
   the RTR probe states `cap.rtr=<rloc>@<cores>@<mem_mb>`. Every member elects the same RTR from the records:
   capability first, identity as the tie-breaker. One pure function in src/rina_elect.hpp, unit-tested.
3. **The NATed machine opens its link to the RTR itself.** It is a TCP session from inside the NAT, outward: an RMT
   link of a new kind, `tcp`. The NAT keeps the binding because the session is open. If the session ends, the
   machine knows at once (liveness is the session), and so does the RTR. No keepalive timer is added; the NAT's own
   TCP idle timeout is a property of the transport, recorded.
4. **The NATed machine's RLOC is the RTR.** Its own record states `via=<rtr machine>`. Route derivation treats it as
   reachable only through the RTR: the RTR is the next machine for every PDU to it, and the RTR forwards over the
   session.
5. **The RTR relays both ways.** PDUs from the NATed machine arrive on the session and are forwarded like any
   others. PDUs to it are sent down the session. Nothing else changes for programs: a flow's peer is still
   `{dif, machine, pid, port}`.

## The experiment (exp-nat.sh)

**Topology** (all in network namespaces):

- y sits behind a NAT namespace (iptables MASQUERADE).
- x is directly reachable.
- root is the run's own machine, which also hosts the region and mapping.
- y cannot be reached from x except through a connection y itself opens.

**What must happen, with nothing about NAT configured anywhere:**

1. y's probe finds it is behind NAT.
2. x is elected RTR.
3. y opens its session up to x.
4. y's record says `via=x`.

**Pass:** a reliable flow x → y and y → x, 0 lost, and a new flow to y by name.

**Fail:** if any NAT fact is configured; if y is reached other than through x; or if any UDP control packet or
periodic packet is seen.

## Open questions, said now

- **The NAT's TCP idle timeout.** Linux conntrack's established TCP timeout is 5 days, so it is not reached in tests.
  On a real NAT that drops idle TCP sooner, the session ends, both ends know at once, and y re-dials on that event.
  Re-dialling is a reaction to a loss, not a timer.
- **More than one RTR, and a NATed machine reaching another NATed machine.** Both go through the RTR, which gives two
  hops. Fine for the claim; a direct path would need hole punching, which needs timing, which is out of scope.
