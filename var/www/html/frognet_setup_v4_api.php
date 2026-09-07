<?php
/***************************************************************
*   Copyright (C) 2016-2026 Fawcett Innovations LLC          *
*                                                            *
*   SPDX-License-Identifier: GPL-2.0-only                    *
*                                                            *
*   This program is free software; you can redistribute it   *
*   and/or modify it under the terms of the GNU General      *
*   Public License as published by the Free Software         *
*   Foundation; version 2 of the License, and no other       *
*   version.                                                 *
*                                                            *
*   See COPYRIGHT and LICENSE at the root of this tree.      *
**************************************************************/

/**
 * frognet_setup_v4_api.php - DISABLED. [SETUP_API_OFF_V1]
 *
 * The web-based setup surface has been removed. It ran setup helpers as root
 * via a www-data sudoers grant with no page password, on the theory that the
 * FrogNet LAN was a sufficient trust boundary. It was not: a browser on the
 * LAN could be made to drive it cross-origin, and its values reached a
 * root-sourced config. Rather than carry that surface, it is off.
 *
 * Node setup is now done on the box itself: edit /etc/frognet/tunnel.conf and
 * run the helpers directly, or use the CLI. The matching sudoers grant is not
 * installed, so even if this file were replaced, www-data could not run the
 * helpers as root.
 *
 * This stub answers every request with 410 Gone and calls nothing.
 */
header('Content-Type: application/json');
header('Cache-Control: no-store');
http_response_code(410);
echo json_encode([
    'ok'    => false,
    'error' => 'The web setup API is disabled. Configure this node on the host '
             . '(edit /etc/frognet/tunnel.conf and run the setup helpers, or use the CLI).',
]);
