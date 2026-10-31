#include "ribbit_space.hpp"
#include <iostream>
int main(int argc,char**argv){if(argc!=2)return 2;ribbit::Space host(std::string("here:127.0.0.1:")+argv[1],"senz-binding");std::cout<<"READY"<<std::endl;std::string line;std::getline(std::cin,line);}
