#include "version.hpp"
#include "frogram.hpp"
#include <iostream>
int main(int argc,char**argv){
 if(argc!=6){std::cerr<<"usage: check-manifest HOST PORT KIND IID GROUP\n";return 2;}
 frogram::Session s(argv[1],std::stoi(argv[2]),LISPER_API); frogram::Memory m(s,LISPER_API);
 std::string v=std::string(argv[3])+"-lengths|"+argv[4]+"|"+argv[5];
 auto cells=m.read("lisp",v);
 for(auto&c:cells)std::cout<<c.instance<<"\n";
 return 0;
}
