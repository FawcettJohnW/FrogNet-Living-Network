<?php
$event = $_GET['event'] ?? '';
if ($event === '') {
    http_response_code(400);
    exit;
}

/*
 * IMPORTANT:
 * Dedup is owned by /usr/local/bin/propogateNotificationInternal.
 * Do NOT mark "seen" here, or the internal script will exit immediately.
 */

@mkdir("/run/frognet/seen_notifications", 0700, true);

// Fire and forget — do NOT block
exec("systemctl start frognet-connectivity-watchdog.service >/dev/null 2>&1 &");
exec("/usr/local/bin/propogateNotificationInternal " . escapeshellarg($event) . " >/dev/null 2>&1 &");

http_response_code(200);
echo "OK\n";
