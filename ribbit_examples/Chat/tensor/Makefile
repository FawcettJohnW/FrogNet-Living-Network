# libfrogram_tensor.so -- the tensor plane in C++ behind a C ABI (Experiment A).
libfrogram_tensor.so: frogram_tensor.cpp ../cpp/frogram.cpp ../cpp/frogram.hpp
	$(CXX) -std=c++11 -O2 -Wall -Wextra -fPIC -shared -pthread frogram_tensor.cpp ../cpp/frogram.cpp -o $@ -lz
clean:
	rm -f libfrogram_tensor.so
