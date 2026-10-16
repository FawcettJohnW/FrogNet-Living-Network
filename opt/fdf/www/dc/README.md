# DisasterComm add-ins — first release

[DC_APPS_V1] The first demonstration: deploy the network, register a help request, locate an available responder,
identify receiving hospital capacity, and coordinate through chat.

Every app is a web page a node serves; any phone or laptop on the node's Wi-Fi uses it in a browser, nothing to
install. Pages reach the area's RAM through `ram.php`, which runs the `ram` CLI the same way `../fdf/chat_*.php` do.

## Who writes what

Every truth has one owner, and the owner writes its own cell. Nobody edits anybody else's record; a reader combines
the cells it watches. The instance names the owner.

| Cell (service.variable.instance) | Written by | Bag |
| --- | --- | --- |
| `help.request.<rid>` | the person registering (or someone on their behalf) | `{name, people, needs[], status, location, contact, note, at}` — status `open` / `safe` / `resolved` |
| `help.action.<rid>~<unit>~<by>` | whoever acts on request `rid` for `unit`: the coordinator who assigns, the unit as it progresses (`by` names the writer, so each writer owns its cell) | `{unit, action, by, hospital, note, at}` — action `assigned` / `en-route` / `on-scene` / `assisted` / `transported` / `released` |
| `resp.unit.<unit>` | the team, ambulance, equipment holder or consenting individual | `{kind, caps[], available, crew, location, contact, at}` |
| `hosp.status.<facility>` | the hospital | `{receiving, caps[], beds{er,icu,general,peds}, location, contact, note, at}` — receiving `open` / `limited` / `diversion` / `closed` |
| `dc.apps.<node>` | each node, at start | `{node, apps:[{id, title, version, page}], at}` — what that node serves |
| `chat.inbox.<sender>`, `chat.room.<n>` | the existing FD&F chat (`../fdf/chat.html`, `chat-service.py`) | unchanged |

`location` is `{lat, lon, text}`; either part may be empty (a typed place is enough).
`at` is the writer's clock in epoch seconds; readers show it as age.

A request's state is read, not stored: its own status plus the newest action from each unit. Two coordinators
assigning at once each write their own `help.action` cell; both are seen. A unit's newest action says whether it is
still committed.

## Watching

A page reads a variable once (`after` = none), then holds a read with `after` = the newest id it has seen. The RAM
answers when a newer cell exists (Ribbit's held read, up to its maximum wait), and the page holds again. No polling.

## Add-ins

Each app is a directory entry in `apps.json` (id, title, version, page, services). The launcher (`index.html`) lists
this node's add-ins and every `dc.apps.*` cell, so apps another connected network serves show up as networks join.
`ram.php` only touches the services an installed add-in declares.
