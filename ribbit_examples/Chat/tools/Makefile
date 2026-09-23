# flood: one writer, one reader.   mesh: N clients all cross-posting at once.
CXXFLAGS = -std=c++11 -O2 -Wall -Wextra -pthread -I../cpp
all: flood mesh
flood: flood.cpp ../cpp/frogram.cpp ../cpp/frogram.hpp
	$(CXX) $(CXXFLAGS) flood.cpp ../cpp/frogram.cpp -o $@
mesh: mesh.cpp ../cpp/frogram.cpp ../cpp/frogram.hpp
	$(CXX) $(CXXFLAGS) mesh.cpp ../cpp/frogram.cpp -o $@
clean:
	rm -f flood mesh
frogbench: frogbench.cpp ../cpp/frogram.cpp ../server_cpp/ram_server.cpp ../cpp/frogram.hpp ../server_cpp/ram_server.h
	$(CXX) $(CXXFLAGS) -DFROGBENCH_EMBED frogbench.cpp ../cpp/frogram.cpp ../server_cpp/ram_server.cpp -o $@

# frogbench.exe -- Windows build (mingw-w64, posix threads). Static, links ws2_32.
frogbench.exe: frogbench.cpp ../cpp/frogram.cpp ../server_cpp/ram_server.cpp ../cpp/frogram.hpp ../server_cpp/ram_server.h
	x86_64-w64-mingw32-g++-posix -std=c++11 -O2 -static -DFROGBENCH_EMBED -I../cpp frogbench.cpp ../cpp/frogram.cpp ../server_cpp/ram_server.cpp -o $@ -lws2_32
