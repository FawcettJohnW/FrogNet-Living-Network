#pragma once
#define EVP_MAX_MD_SIZE 64
struct EVP_MD { int unused; };
inline const EVP_MD* EVP_sha256() { static const EVP_MD m{0}; return &m; }
