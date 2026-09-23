# reflector_server: the fast-plane one-to-many reflector, in one process.
CXXFLAGS = -std=c++11 -O2 -Wall -Wextra -pthread -I../cpp
reflector_server: reflector_server.cpp ../cpp/frogram.cpp ../cpp/frogram.hpp
	$(CXX) $(CXXFLAGS) reflector_server.cpp ../cpp/frogram.cpp -o $@
test_reflector: test_reflector_oracle.cpp reflector_client.cpp reflector_client.hpp
	$(CXX) $(CXXFLAGS) test_reflector_oracle.cpp reflector_client.cpp -o $@
windows: reflector_server.cpp ../cpp/frogram.cpp
	x86_64-w64-mingw32-g++-posix $(CXXFLAGS) -static reflector_server.cpp ../cpp/frogram.cpp -o reflector_server.exe -lws2_32
clean:
	rm -f reflector_server test_reflector reflector_server.exe
