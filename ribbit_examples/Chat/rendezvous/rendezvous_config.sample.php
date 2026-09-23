<?php
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Copy to rendezvous_config.php and set RZ_PUBLIC_HOST. install_rendezvous.sh does this.
define('RZ_PUBLIC_HOST',   'fawcettinnovations.com'); // what a client dials -- the broker's public name
define('RZ_BIND_HOST',     '0.0.0.0');                // ram_server binds this; a demo faces the Internet
define('RZ_RAM_SERVER',    '/opt/frogram-rendezvous/ram_server');
define('RZ_REFLECTOR',     '/opt/frogram-rendezvous/reflector_server'); // the fast plane; set '' to disable
define('RZ_WATCHDOG',      '/opt/frogram-rendezvous/ram_watchdog.sh');
define('RZ_STATE_DIR',     '/opt/frogram-rendezvous/state');
define('RZ_PORT_LOW',      22874);                    // a random free port in this range is chosen per tag
define('RZ_PORT_HIGH',     65535);                    // open this range once, in ufw and the cloud firewall
define('RZ_MAX_SERVERS',   50);                       // <= the range size; the pool cap
define('RZ_IDLE_TIMEOUT_S', 900);                     // an unused tag frees its port after this
// The demo passwords you hand out. A request must carry one of these as pass=...
// Edit this list to add or revoke; no restart needed, PHP reads it per request.
define('RZ_PASSWORDS', ['FrogNetD3mo!', 'ShrredR4M!']);
