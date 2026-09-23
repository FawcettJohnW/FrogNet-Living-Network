CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Wno-unused-parameter -pthread
SRC = src/memory.cpp src/ebr.cpp src/resp.cpp src/util.cpp src/db.cpp src/server.cpp src/cmd_conn.cpp src/cmd_server.cpp src/cmd_keyspace.cpp src/cmd_string.cpp src/cmd_hash.cpp src/cmd_set.cpp src/cmd_list.cpp src/cmd_multi.cpp src/cmd_sort.cpp src/cmd_zset.cpp src/cmd_geo.cpp src/cmd_hll.cpp src/cmd_stream.cpp src/cmd_stream_groups.cpp
OBJ = $(SRC:.cpp=.o)
all: ribbit-redis
ribbit-redis: $(OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $(OBJ)
src/%.o: src/%.cpp src/server.hpp src/memory.hpp src/pmap.hpp src/hmap.hpp src/resp.hpp src/cmdtable.hpp src/stream.hpp src/blocking.hpp src/lfmap.hpp src/ebr.hpp src/cfgtable.hpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<
# src/cmdtable.hpp is generated and checked in. Regenerate it only on request:
#   make cmdtable REDIS_SRC=/path/to/redis-7.2.11
cmdtable:
	python3 tools/gen_cmdtable.py $(REDIS_SRC) src/cmdtable.hpp
clean:
	rm -f $(OBJ) ribbit-redis
.PHONY: all clean cmdtable
