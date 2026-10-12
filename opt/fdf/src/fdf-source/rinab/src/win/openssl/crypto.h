#pragma once
#define OPENSSL_INIT_NO_ATEXIT 0
inline int OPENSSL_init_crypto(unsigned long long, const void*) { return 1; }
