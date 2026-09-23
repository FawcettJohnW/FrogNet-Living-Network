# frogchat, C++.
#   make                      Linux binary for THIS machine's architecture  -> $(OUT)/frogchat
#   make windows              cross-build with mingw-w64 (posix threads)     -> $(OUT)/frogchat.exe
#                             (sudo apt-get install g++-mingw-w64-x86-64-posix)
# OUT defaults to this directory; the packager points it at a scratch directory.
LIB      ?= ../../../cpp
OUT      ?= .
CXXFLAGS  = -std=c++11 -O2 -Wall -Wextra -I$(LIB)
SRC       = frogchat.cpp $(LIB)/frogram.cpp

$(OUT)/frogchat: $(SRC) $(LIB)/frogram.hpp
	$(CXX) $(CXXFLAGS) -pthread -static-libstdc++ -static-libgcc $(SRC) -o $@

windows: $(OUT)/frogchat.exe
$(OUT)/frogchat.exe: $(SRC) $(LIB)/frogram.hpp
	x86_64-w64-mingw32-g++-posix $(CXXFLAGS) -static $(SRC) -o $@ -lws2_32

clean:
	rm -f $(OUT)/frogchat $(OUT)/frogchat.exe
.PHONY: windows clean
