# v0.45 RED — site-policy change must re-govern native registrations

Base: v0.44 GREEN (87d9a8cc1a0226610e3f78027db44d5376bc25b1eff9b285d853487182904bf6).
tools/test_native_regovernance.py: an accepted native registration must stop resolving when its authorizing site is
deleted, resolve again when the site is restored, and not be authorized by a site that does not cover it — with no
action by the ETR. Against v0.44: after site.delete the ITR still resolves it
(resolver.wait present=False times out; artifacts/native-regovernance-red-v0.45.txt). No implementation source changed.
