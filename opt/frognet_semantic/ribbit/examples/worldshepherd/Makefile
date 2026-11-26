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

# ---- for the nodes: static binaries, one directory per machine type, aarch64 FIRST (John, 2026-11-25: "Do aarch64
# before x86 all the time"). [NODE_BINARIES_V1] A node has no compiler and needs none: `make node` builds both here.
#   make node       node/aarch64/{worldshepherd-ram,libkartram.so}, then node/x86_64/{worldshepherd-ram,libkartram.so}
#   make tarballs   dist/worldshepherd-node-<build>-aarch64.tgz, then -x86_64.tgz (node/package.sh; one a machine type)
# aarch64 needs g++-aarch64-linux-gnu, and lz4 and OpenSSL cross-built once by rinab/tools/build_arm64.sh ($(ARM)).
# x86_64 needs liblz4-dev, libssl-dev and libjemalloc-dev for their .a files. The host is fully static on both. The
# library Python loads cannot be: it has libstdc++, lz4 (and on aarch64 OpenSSL) inside and needs the node's glibc
# 2.34 or later (and on x86_64 its libcrypto.so.3), as rinab/kart/build_kartram.sh's does.
RINAB := ../../../../fdf/src/fdf-source/rinab
ARM   ?= $(RINAB)/build/arm-deps
ACXX  := aarch64-linux-gnu-g++
AFLAGS := -std=c++20 -O2 -pthread -D_GNU_SOURCE -I$(PL)/include -I$(ARM)/lz4/lib -I$(ARM)/openssl/include
ALIBS := $(ARM)/lz4/lib/liblz4.a $(ARM)/openssl/libcrypto.a
XL    := /usr/lib/x86_64-linux-gnu
SOFLAGS := -shared -pthread -fvisibility=hidden -static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL -Wl,--version-script=$(KART)/kartram.map -Wl,--no-undefined
HDRS  := $(wildcard $(PL)/include/*.hpp)

node: node/aarch64/worldshepherd-ram node/aarch64/libkartram.so node/x86_64/worldshepherd-ram node/x86_64/libkartram.so
	@file node/aarch64/worldshepherd-ram node/x86_64/worldshepherd-ram | sed 's/, BuildID.*//'
	@for a in aarch64 x86_64; do echo "node/$$a/libkartram.so needs glibc $$(objdump -T node/$$a/libkartram.so 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1 || aarch64-linux-gnu-objdump -T node/$$a/libkartram.so | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1) or later"; done
	@echo "NODE BUILD OK: $$(cat VERSION | cut -d' ' -f1-2), aarch64 and x86_64"

# ---- the session library checked on each machine type without Python: build/<type>/session_check drives
# node/<type>/libkartram.so against node/<type>/worldshepherd-ram; aarch64 runs under qemu-aarch64-static, first.
#   make session-check [SC_TICKETS=1000] [SC_CLAIMANTS=7]
SC_TICKETS ?= 1000
SC_CLAIMANTS ?= 7
SC_API := /FawcettInnovations.RINA.ram_interface.php
build/a64/session_check: harness/session_check.cpp node/aarch64/libkartram.so | build/a64
	$(ACXX) -std=c++20 -O2 -pthread -o $@ $< node/aarch64/libkartram.so -Wl,-rpath,'$$ORIGIN/../../node/aarch64'
build/x64/session_check: harness/session_check.cpp node/x86_64/libkartram.so | build/x64
	$(CXX) -std=c++20 -O2 -pthread -o $@ $< node/x86_64/libkartram.so -Wl,-rpath,'$$ORIGIN/../../node/x86_64'
session-check: build/a64/session_check build/x64/session_check node/aarch64/worldshepherd-ram node/x86_64/worldshepherd-ram
	@for t in "aarch64 a64 qemu-aarch64-static -L /usr/aarch64-linux-gnu" "x86_64 x64"; do set -- $$t; a=$$1; b=$$2; shift 2; \
	  port=$$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'); \
	  $$@ node/$$a/worldshepherd-ram --listen 127.0.0.1:$$port --api $(SC_API) >/dev/null 2>&1 & ram=$$!; sleep 3; \
	  echo "== $$a: $(SC_TICKETS) tickets, $(SC_CLAIMANTS) claimant sessions"; \
	  $$@ build/$$b/session_check 127.0.0.1 $$port $(SC_API) $(SC_TICKETS) $(SC_CLAIMANTS); r=$$?; kill $$ram; wait $$ram 2>/dev/null; \
	  [ $$r = 0 ] || exit $$r; done
.PHONY: session-check

build/a64 build/a64pic build/x64 build/x64pic node/aarch64 node/x86_64 dist:
	mkdir -p $@

$(ARM)/openssl/libcrypto.a $(ARM)/lz4/lib/liblz4.a:
	@echo "$@ is not there: run $(RINAB)/tools/build_arm64.sh once (it cross-builds lz4 and OpenSSL for aarch64)"; false

# aarch64
build/a64/%.o: $(PL)/src/%.cpp $(HDRS) $(ALIBS) | build/a64
	$(ACXX) $(AFLAGS) -c $< -o $@
build/a64/worldshepherd_ram.o: worldshepherd_ram.cpp $(HDRS) $(ALIBS) | build/a64
	$(ACXX) $(AFLAGS) $(WARN) -c $< -o $@
build/a64pic/%.o: $(PL)/src/%.cpp $(HDRS) $(ALIBS) | build/a64pic
	$(ACXX) $(AFLAGS) -fPIC -fvisibility=hidden -c $< -o $@
build/a64pic/%.o: $(KART)/%.cpp $(HDRS) $(ALIBS) | build/a64pic
	$(ACXX) $(AFLAGS) -fPIC -fvisibility=hidden -c $< -o $@
build/a64pic/glibc_shim.o: node/glibc_shim.cpp | build/a64pic
	$(ACXX) $(AFLAGS) -fPIC -c $< -o $@
node/aarch64/worldshepherd-ram: build/a64/worldshepherd_ram.o build/a64/ram_host.o build/a64/frogram.o build/a64/lf_ebr.o | node/aarch64
	$(ACXX) -static -pthread $^ $(ALIBS) -lresolv -ldl -o $@
node/aarch64/libkartram.so: build/a64pic/kartram.o build/a64pic/kartram_glibc.o build/a64pic/glibc_shim.o build/a64pic/frogram.o build/a64pic/lf_ebr.o | node/aarch64
	$(ACXX) $(SOFLAGS) $^ $(ALIBS) -lresolv -o $@

# x86_64
build/x64/%.o: $(PL)/src/%.cpp $(HDRS) | build/x64
	$(CXX) $(CXXFLAGS) -c $< -o $@
build/x64/worldshepherd_ram.o: worldshepherd_ram.cpp $(HDRS) | build/x64
	$(CXX) $(CXXFLAGS) $(WARN) -c $< -o $@
build/x64pic/%.o: $(PL)/src/%.cpp $(HDRS) | build/x64pic
	$(CXX) $(CXXFLAGS) -fPIC -fvisibility=hidden -c $< -o $@
build/x64pic/%.o: $(KART)/%.cpp $(HDRS) | build/x64pic
	$(CXX) $(CXXFLAGS) -fPIC -fvisibility=hidden -c $< -o $@
build/x64pic/glibc_shim.o: node/glibc_shim.cpp | build/x64pic
	$(CXX) $(CXXFLAGS) -fPIC -c $< -o $@
node/x86_64/worldshepherd-ram: build/x64/worldshepherd_ram.o build/x64/ram_host.o build/x64/frogram.o build/x64/lf_ebr.o | node/x86_64
	$(CXX) -static -pthread $^ $(XL)/libjemalloc.a $(XL)/liblz4.a $(XL)/libcrypto.a -lresolv -ldl -o $@
node/x86_64/libkartram.so: build/x64pic/kartram.o build/x64pic/kartram_glibc.o build/x64pic/glibc_shim.o build/x64pic/frogram.o build/x64pic/lf_ebr.o | node/x86_64
	$(CXX) $(SOFLAGS) $^ $(XL)/liblz4.a -lcrypto -lresolv -o $@

tarballs: node | dist
	bash node/package.sh aarch64
	bash node/package.sh x86_64

clean:
	rm -rf build lib bin/worldshepherd-ram node/aarch64 node/x86_64 dist

.PHONY: all clean node tarballs
