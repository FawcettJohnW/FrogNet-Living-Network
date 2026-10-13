# AI_README — the store's HTTP face

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

`api.php` is the tuple store's interface. Everything a node reads or writes to
FrogNet Memory arrives here as an HTTP request.

| | |
|---|---|
| `api.php` | `entity=sensor_data&action=upsert_by_name` for writes; `entity=sensors&action=values` for reads |
| `frognet_node.inc.php` | every field of the node snapshot, one source per field per mode (its header has the table) |
| `getSnapshot.php` | the node snapshot: build, mode, ponds, members, hub link, elections, errors. The monitors and the dashboard read only this |
| `getHosts.php` `frognet_echo.php` | the members as rows, and this node's echo line (the same functions as the snapshot) |
| `index.php` `fln_dashboard32.html` | the landing page and the dashboard (`docs/OPERATING.md` section 5) |

## Traps

- **This is the memory, not a REST API.** It looks like one. Describing FrogNet's
  programming model in terms of these endpoints reproduces exactly the model
  FrogNet replaces. The application-facing surface is `core/frognet_tuples.py`.
- **The unique key is `SensorName + SensorType + SensorAddress`.** `SensorName`
  carries the var *and* the scope; `SensorType` is the service. Two roles written
  under a bare node scope collide, which is why role scope exists.
- **`UpdatedAt` is the freshness clock and it belongs to the store.** A read
  passes a window and the store filters on the row's own update time. A
  timestamp inside the payload is never consulted for freshness.
- **A write is an atomic upsert.** Resolve-or-create in one statement. Do not
  split it into a select and an insert.
