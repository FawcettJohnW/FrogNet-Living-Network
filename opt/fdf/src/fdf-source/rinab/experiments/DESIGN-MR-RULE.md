# DESIGN-MR-RULE: the Map-Resolver's address, derived from the area's name (John, 2026-10-07: option B)

**The rule (`[MR_ADDRESS_FROM_NAME_V1]`).** `h = SHA-256(area name, as UTF-8 bytes)`. The Map-Resolver for the area is
at a fixed prefix, plus h's leading bits as the host part. Every machine computes the same address from the name;
nothing is configured, and DNS is not used.

| form | prefix | host bits | example: rina-area.test |
|---|---|---|---|
| IPv6 (deployment) | fd46:4446::/32 | the first 96 bits of h | computed by the tool |
| IPv4 (this sandbox: no IPv6 in its kernel) | 198.18.0.0/15 (RFC 2544 benchmarking block) | the first 17 bits of h, avoiding .0 and .255 | computed by the tool |

- **Who binds it.** The machine that serves the area's mapping region adds the derived address to an interface and
  listens there. Anycast: more than one machine may bind it, and routing picks the nearest.
- **Who uses it.** Any machine given the area's name, when `RINA_MAP_RESOLVER` is not set. Configuring it stays
  possible, but becomes an override, not a requirement.
- **Open for John:** the IPv6 prefix. fd46:4446 is a placeholder in the unique-local range ("FD" "FD&F").
