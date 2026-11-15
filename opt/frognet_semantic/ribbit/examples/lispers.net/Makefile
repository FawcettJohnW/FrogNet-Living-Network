# Ribbit-LISP: make builds only what changed.
#   make -j$(nproc)                 everything the nodes run
#   make -j$(nproc) front           Seattle7-style systems machine: the front (tools/lisp-boundary)
#   make -j$(nproc) ram             the RAM host: ribbit_cpp/lisper-ram
# The Ribbit platform (../../cpp/src) is compiled once into build/libribbit.a and recompiled only when a platform
# source or header changes. -MMD -MP: every object knows the headers it includes, so a header change rebuilds
# exactly the objects that include it.

R        := ../../cpp
CXX      ?= g++
CXXFLAGS ?= -std=c++20 -O2 -pthread -fno-omit-frame-pointer
CPPFLAGS := -Iribbit_cpp -Iclient -I$(R)/include -MMD -MP
LDLIBS   := -lcrypto -llz4
B        := build/obj

PLATFORM := frogram ram_host lf_ebr ribbit_space ribbit_pp ram_client
PLAT_OBJ := $(addprefix $(B)/plat/,$(addsuffix .o,$(PLATFORM)))

PROGRAMS := tools/lisp-boundary ribbit_cpp/lisper-ram ribbit_cpp/ribbit-lisp tools/lisp-handler-driver tools/lisp-service \
            tools/check-ms-secret tools/check-site-secret

.PHONY: all front ram clean
all: $(PROGRAMS)
front: tools/lisp-boundary ribbit_cpp/ribbit-lisp
ram: ribbit_cpp/lisper-ram ribbit_cpp/ribbit-lisp

build/libribbit.a: $(PLAT_OBJ)
	ar rcs $@ $^

$(B)/plat/%.o: $(R)/src/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $(CPPFLAGS) -c $< -o $@

$(B)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $(CPPFLAGS) -c $< -o $@

# Each program: its own object, linked against the platform library. The linker takes only what it uses.
tools/lisp-boundary:        $(B)/tools/lisp_boundary.o        build/libribbit.a
ribbit_cpp/lisper-ram:      $(B)/ribbit_cpp/lisper_ram.o      build/libribbit.a
ribbit_cpp/ribbit-lisp:     $(B)/ribbit_cpp/ribbit_lisp.o     build/libribbit.a
tools/lisp-handler-driver:  $(B)/tools/lisp_handler_driver.o  build/libribbit.a
tools/lisp-service:         $(B)/tools/lisp_service.o         build/libribbit.a
tools/check-ms-secret:      $(B)/tools/check_ms_secret.o      build/libribbit.a
tools/check-site-secret:    $(B)/tools/check_site_secret.o    build/libribbit.a
$(PROGRAMS):
	$(CXX) $(CXXFLAGS) $^ $(LDLIBS) -o $@

clean:
	rm -rf build/obj build/libribbit.a $(PROGRAMS)

-include $(shell find $(B) -name '*.d' 2>/dev/null)
