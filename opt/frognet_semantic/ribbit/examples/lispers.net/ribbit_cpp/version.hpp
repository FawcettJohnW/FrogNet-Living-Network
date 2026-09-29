// The ONE definition of this package's version. Every program prints it (--version, and its startup line); the RAM
// host answers it (op=version); tools/qualify.sh reads it from here and prints it in a banner at the top of every run,
// with the version of the RAM host it is talking to.
#pragma once
#define RIBBIT_LISP_VERSION "v0.64-vendor-ram"
// The LISP region's endpoint on its RAM host (lisper-ram). The vendor names it; every LISP program uses it.
#define LISPER_API "/lisper-api"
