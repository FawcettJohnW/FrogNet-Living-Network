# v0.18 source audit checkpoint

Status: audit-only; source behavior unchanged from v0.17.

## Request/read-on-demand findings

- `resolution.get`: held registration + map-server governance. Green; zero warmed request RAM reads proven.
- registration authorization: held public site policy. Green; zero warmed authorization RAM reads proven.
- `ddt.get`: **remaining authoritative LPM request-time RAM scan** via `ram_mappings("ddt-delegation", iid, group)`. Convert next.
- `ddt.delete`: physical `remove(id)` via `ram_delete`; incompatible with a held consumer because removal supplies no later truth. Convert to inactive current-state truth.
- `map_cache.get` / `map_cache.list`: request-time RAM reads remain. This is an ITR-side explicitly managed cache/API surface, not resolver authoritative truth; classify separately after DDT.
- `map_resolver.get` / `map_resolver.delete`: direct RAM reads; configuration/API surface, not LPM data-plane resolution. Classify separately after DDT.
- `database_mapping.delete` / `map_cache.delete`: physical removal remains. These are explicit managed surfaces, but held-consumer semantics must be reconsidered if/when they are held.
- `registration.put`: scans the registration `/N` variable to determine prior source-owned truth for TTL-0 authorization/refresh/merge behavior. This is writer-side decision logic, not request-time resolution. Do not delete blindly; determine whether writer can hold/own the needed truth without violating independent xTR ownership.

## Next conversion

DDT authoritative delegation truth:
- one current delegation truth per prefix cell;
- per-prefix-length variables;
- monotonic known-prefix-length manifest;
- one held DDT participant/view per `(iid, group)`;
- held manifest watcher discovers new `/N` variables;
- held `/N` watchers advance by record id;
- `ddt.get` performs LPM entirely against held local truth;
- `ddt.delete` publishes inactive truth, not physical removal;
- same-process writes visible only through RAM wake path;
- writer does not wait for reader;
- add explicit convergence and zero-request-read proof.
