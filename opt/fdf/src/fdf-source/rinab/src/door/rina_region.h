// The RINA region's entry points for the RAM host: a JSON body in, a JSON body out.
#pragma once
#include <cstdint>
#include <string>
std::string rina_region_call(const std::string& body);    // inline: never waits
std::string rina_region_block(const std::string& body);   // may wait, on its own request thread
uint64_t rina_session_id();                               // [SESSION_IS_LIVENESS_V1] (from the patched server)
void rina_session_down(uint64_t id);                      // called once when a session's socket closes
// [OWNED_CELL_GOES_WITH_THE_SESSION_V1] this request's session owns s/v/i: written {} when the session ends
void rina_own_cell(const std::string& s, const std::string& v, const std::string& i);
// [PERMANENT_CELL_V1] s/v/i = bag is filed in the region's file and written back into the memory at the next start
void rina_permanent_cell(const std::string& s, const std::string& v, const std::string& i, const std::string& bag);
#include <vector>
#include "frogram.hpp"
frogram::MemoryApi& rina_resident_memory();               // [RINA_RESIDENT_V1] the memory itself, in this process
// a held read on the memory itself: rows of s/v/i written past `after`; waits up to wait_s for the first one
std::vector<frogram::Cell> rina_resident_wait(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s);
// [RAW_HELD_READ_V1] the held read with the cells as stored: (id, bag text), nothing parsed or rebuilt
std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s);
// [RAW_GET_V1] one cell as stored: its bag text (false if there is no such cell)
bool rina_resident_get_raw(const std::string& s, const std::string& v, const std::string& i, std::string& bag);
// [SDU_BY_REFERENCE_V1] one cell as stored, with its id
bool rina_resident_get_raw_id(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id);
// [DEFERRED_ANSWER_V1] a region operation that cannot answer yet takes this request's continuation (null outside a
// request), and whoever makes the answer exist sends it with rina_answer -- no thread waits for it
void* rina_defer();
void rina_answer(void* continuation, const std::string& body);
// [DAF_MODEL_V1] John Day's DAF model as a region (src/daf_region.cpp): op=daf, inline, never waits
std::string daf_region_call(const std::string& body);
void daf_session_down(uint64_t id);
void daf_start();
