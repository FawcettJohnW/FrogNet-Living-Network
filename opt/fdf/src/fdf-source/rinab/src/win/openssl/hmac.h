#pragma once
#include "sha.h"
#include "evp.h"
inline unsigned char* HMAC(const EVP_MD*, const void* key, int klen, const unsigned char* d, size_t n, unsigned char* md, unsigned int* mdlen) {
    fdf_cng_sha256(true, key, (unsigned long)klen, d, n, md); if (mdlen) *mdlen = SHA256_DIGEST_LENGTH; return md;
}
