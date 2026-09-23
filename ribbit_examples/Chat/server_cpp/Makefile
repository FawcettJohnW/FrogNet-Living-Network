# ram_server: one service's FrogNet RAM (FNW1 + the memory) in one C++ process.
ram_server: ram_server.cpp ../cpp/frogram.cpp ../cpp/frogram.hpp
	$(CXX) -std=c++11 -O2 -Wall -Wextra -pthread -I../cpp ram_server.cpp ../cpp/frogram.cpp -o $@
clean:
	rm -f ram_server
