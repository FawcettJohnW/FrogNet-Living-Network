// The Redis region's one entry point for the RAM host: a JSON body in, a JSON body out. No Redis type crosses it.
#pragma once
#include <string>
std::string redis_region_call(const std::string& body);
// A blocking command (BLPOP, XREAD BLOCK, DEBUG SLEEP ...): parks, on the caller's own request thread.
std::string redis_region_block(const std::string& body);
