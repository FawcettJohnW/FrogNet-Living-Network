// [FDF_WINDOWS_CLIENT_V1] Windows-only stand-in for the two OpenSSL calls the FNWP client uses, over Windows' own CNG
// (bcrypt, documented since Vista): SHA256() and HMAC(EVP_sha256(), ...). Used only by the Windows build (-Isrc/win).
#pragma once
#include <windows.h>
#include <bcrypt.h>
#include <cstddef>
#define SHA256_DIGEST_LENGTH 32
inline BCRYPT_ALG_HANDLE fdf_cng_provider(bool hmac) {           // opened once per kind, kept for the process
    static BCRYPT_ALG_HANDLE h[2] = {nullptr, nullptr};
    BCRYPT_ALG_HANDLE& p = h[hmac ? 1 : 0];
    if (!p) BCryptOpenAlgorithmProvider(&p, BCRYPT_SHA256_ALGORITHM, nullptr, hmac ? BCRYPT_ALG_HANDLE_HMAC_FLAG : 0);
    return p;
}
inline void fdf_cng_sha256(bool hmac, const void* key, unsigned long klen, const unsigned char* d, size_t n, unsigned char* md) {
    BCRYPT_HASH_HANDLE hh = nullptr;
    BCryptCreateHash(fdf_cng_provider(hmac), &hh, nullptr, 0, static_cast<PUCHAR>(const_cast<void*>(key)), hmac ? klen : 0, 0);
    BCryptHashData(hh, const_cast<PUCHAR>(d), ULONG(n), 0);
    BCryptFinishHash(hh, md, SHA256_DIGEST_LENGTH, 0);
    BCryptDestroyHash(hh);
}
inline unsigned char* SHA256(const unsigned char* d, size_t n, unsigned char* md) { fdf_cng_sha256(false, nullptr, 0, d, n, md); return md; }
