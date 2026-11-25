# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Makefile -- WorldShepherd on the memory: its RAM host (the platform's, with WorldShepherd's rules about its own cells:
# worldshepherd_ram.cpp) and the session library Python loads. The RAM is a FrogNet app service: fdf-app serves
# bin/worldshepherd-ram on the machine the network elects (README.md).
#   make            bin/worldshepherd-ram, lib/libkartram.so, lib/ribbit.py
#   make clean
# Incremental: the platform's sources are compiled once into build/ and again only when they change.
# Needs g++ (C++20), liblz4-dev, libssl-dev. This directory is opt/frognet_semantic/ribbit/examples/worldshepherd.
PL   := ../../cpp
KART := ../../../../fdf/src/fdf-source/rinab/kart
CXX  ?= g++
CXXFLAGS := -std=c++20 -O2 -pthread -D_GNU_SOURCE -I$(PL)/include
WARN := -Wall -Wextra -Werror -Wno-free-nonheap-object
LIBS := -llz4 -lcrypto

all: bin/worldshepherd-ram lib/libkartram.so lib/ribbit.py
	@echo "BUILD OK: $$(cat VERSION)"

build bin lib:
	mkdir -p $@

# the platform, for the host (not position-independent) and for the library (position-independent)
build/%.o: $(PL)/src/%.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -c $< -o $@
build/pic_%.o: $(PL)/src/%.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -fPIC -c $< -o $@

build/worldshepherd_ram.o: worldshepherd_ram.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) $(WARN) -c $< -o $@
build/pic_kartram.o: $(KART)/kartram.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -fPIC -c $< -o $@

bin/worldshepherd-ram: build/worldshepherd_ram.o build/ram_host.o build/frogram.o build/lf_ebr.o | bin
	$(CXX) -pthread $^ $(LIBS) -o $@
lib/libkartram.so: build/pic_kartram.o build/pic_frogram.o build/pic_lf_ebr.o | lib
	$(CXX) -shared -pthread $^ $(LIBS) -lresolv -o $@
lib/ribbit.py: $(KART)/ribbit.py | lib
	cp $< $@

clean:
	rm -rf build lib bin/worldshepherd-ram

.PHONY: all clean
