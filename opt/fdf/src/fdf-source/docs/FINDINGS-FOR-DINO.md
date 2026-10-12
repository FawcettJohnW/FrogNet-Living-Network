# Findings in lispers.net from FD&F D5 (2026-10-06)

Found while using unmodified lispers.net (commit in LISPERS-COMMIT) as the Map-Server/Map-Resolver for an area's
distinguished-name EID. Dino's code was not changed; these are offered as reports.

1. **lig and the ITR cannot look up a distinguished-name EID.** `lisp_ecm.encode` builds the inner header only for an
   IPv4 or IPv6 destination. lig (lisp-lig.py) and `lisp_send_map_request` pass the target EID as the inner
   destination, so for a name EID `ip` stays `""`. Under Python 3: `TypeError: can't concat str to bytes` at
   `return(ecm + ip + udp)`. Under Python 2 the ECM would go out with no inner IP header. Workaround used here: the
   requester gives the inner header an IPv4 destination (the Map-Resolver), and lispers.net's MR and MS then answer
   correctly from the Map-Request's EID record.
2. **A Map-Register whose authentication fails is still acknowledged.** The MS logs "Authentication failed for
   EID-prefix ..." and then "Send Map-Notify to ack Map-Register". The registration is not stored (a wrong-key attempt
   to repoint the name did not take), but the Map-Notify tells the registrant nothing. A registrant can tell only by
   checking the Map-Notify's HMAC.
3. **Startup requires IPv6.** lisp-core binds `0::0` unless `lisp_is_raspbian()`, and dies on a kernel without IPv6,
   with the traceback lost (`traceback.print_last(file=fd) failed`, since there is no `sys.last_*` in that path under
   Python 3).
4. **An expired registration is served for up to a minute past its TTL.** With the T bit (use the record's TTL for
   the timeout) and a 5 s TTL, lisp-ms kept answering Map-Requests with the expired RLOC until its site-timeout timer
   came round (`LISP_SITE_TIMEOUT_CHECK_INTERVAL = 60`, `lisp_timeout_sites`). Measured first negative answers: 31.5,
   50.7, 58.8, 58.8 s after registering (tools/map-expiry-compare.sh). Checking the TTL when a Map-Request is answered
   would make expiry exact. FD&F's mapping region does this, and was negative at 5.1 s in every run.
