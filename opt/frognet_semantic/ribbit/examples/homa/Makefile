# Soft Homa: Homa's socket calls on CroakOS memory (DESIGN.md). Incremental: only what changed is rebuilt.
#   make HOMA=/path/to/HomaModule        (homa.h is his, read at 07ce63ef; nothing of his is copied here)
P    := ../../cpp
HOMA ?= $(error set HOMA=/path/to/HomaModule (for homa.h))
CXX  ?= g++
CXXFLAGS := -std=c++20 -O2 -g -pthread -fPIC -Wall -Wextra -Wno-free-nonheap-object -I$(P)/include
PLATFORM := ribbit_space frogram ram_host lf_ebr
OBJS := $(patsubst %,build/%.o,$(PLATFORM))

all: build/libsofthoma.so build/softhoma-ram build/field-order-check

build/%.o: $(P)/src/%.cpp $(wildcard $(P)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -c $< -o $@
build/softhoma.o: src/softhoma.cpp $(P)/include/ribbit_space.hpp $(HOMA)/homa.h | build
	$(CXX) $(CXXFLAGS) -I$(HOMA) -c $< -o $@
build/libsofthoma.so: build/softhoma.o $(OBJS)
	$(CXX) -shared -pthread -o $@ $^ -llz4 -lcrypto -ldl
build/softhoma-ram: src/softhoma_ram.cpp $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ -llz4 -lcrypto
build/field-order-check: tools/field_order_check.cpp $(wildcard $(P)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -o $@ $< -llz4 -lcrypto
build:
	mkdir -p build
clean:
	rm -rf build
.PHONY: all clean
