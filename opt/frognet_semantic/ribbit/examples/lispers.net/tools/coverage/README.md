# P5 coverage (ACCEPTANCE-TEST-PLAN.md)
Ribbit (gcov/gcovr): build ram-server and lisp-boundary with `-O0 -g --coverage -Wl,-u,__gcov_dump`; run
`tools/acceptance.py ... --ram-server <coverage ram-server> --ribbit-front <coverage lisp-boundary>`; both end on
SIGTERM through [COVERAGE_ON_TERM_V1], which writes the data; then
`gcovr --root <package> --object-directory <build dir> --filter <package>/ribbit_cpp/ --exclude-throw-branches -s`.
lispers.net (coverage.py): every lispers.net process measures itself when `COVERAGE_PROCESS_START` names
lispers.coveragerc (coverage.py's own .pth starts it; data written on SIGTERM, which STOP-LISP sends) -- pass
`--lispers-env COVERAGE_PROCESS_START=<path>/lispers.coveragerc`; then, in lispers.net's source directory,
`coverage combine` and `coverage report`/`html` with report.coveragerc (maps the .pyc paths to the sources).
Keep the rc files OUT of the data-file glob (.coverage*) -- a `rm .coverage*` removes the config.
