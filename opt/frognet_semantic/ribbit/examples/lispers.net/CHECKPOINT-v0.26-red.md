# v0.26 negative Map-Reply RED checkpoint

Starting point: verified v0.25 multi-record green.

Authoritative reference: RFC 9301 section 8.3. A Map-Request matching an authoritative/configured EID-prefix with no registered ETR gets a zero-locator Natively-Forward Negative Map-Reply with a 1-minute TTL. A no-match/non-LISP hole uses 15 minutes.

Added a non-vacuous wire test for configured 198.18.88.0/24 with no registration. It requires TTL=1 and the negative EID record to identify 198.18.88.0/24. Current v0.25 behavior fails because wire.request4 treats every resolution miss identically and emits TTL=15 for the requested /32.
