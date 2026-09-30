# v0.42 RED — a registration with no authorizing site must be rejected

Base: v0.41 GREEN (70a4daca992b3ef1c68202d8c3ed60920b4a31f8867e23b748786b4a3b1ea1e7).
New test `test_register_with_no_authorizing_site_is_rejected` (tests/test_site_authorization.py), in an IID with no
sites: a registration is rejected; after a site is added and then deleted (the last site gone), a registration is
still rejected. Against v0.41: 'good' != 'unauthorized', local and clean FNW1
(artifacts/ribbit-local-no-site-red-v0.42.txt, artifacts/ribbit-fnw1-no-site-red-v0.42.txt).
No implementation source changed.
