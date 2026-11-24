# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Makefile -- WorldShepherd on the memory: the session library Python loads. There is no RAM manager to build here:
# WorldShepherd's RAM is a FrogNet app service, served by the platform's fdf-ram (README.md).
#   make            lib/libkartram.so, lib/ribbit.py
#   make clean
# Incremental: the platform's sources are compiled once into build/ and again only when they change.
# Needs g++ (C++20), liblz4-dev, libssl-dev. This directory is opt/frognet_semantic/ribbit/examples/worldshepherd.
PL   := ../../cpp
KART := ../../../../fdf/src/fdf-source/rinab/kart
CXX  ?= g++
CXXFLAGS := -std=c++20 -O2 -pthread -D_GNU_SOURCE -I$(PL)/include
LIBS := -llz4 -lcrypto

all: lib/libkartram.so lib/ribbit.py
	@echo "BUILD OK: $$(cat VERSION)"

build lib:
	mkdir -p $@

# the platform's session library, position-independent
build/pic_%.o: $(PL)/src/%.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -fPIC -c $< -o $@

build/pic_kartram.o: $(KART)/kartram.cpp $(wildcard $(PL)/include/*.hpp) | build
	$(CXX) $(CXXFLAGS) -fPIC -c $< -o $@

lib/libkartram.so: build/pic_kartram.o build/pic_frogram.o build/pic_lf_ebr.o | lib
	$(CXX) -shared -pthread $^ $(LIBS) -lresolv -o $@
lib/ribbit.py: $(KART)/ribbit.py | lib
	cp $< $@

clean:
	rm -rf build lib

.PHONY: all clean
