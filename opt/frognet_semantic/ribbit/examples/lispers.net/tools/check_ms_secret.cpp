#include "version.hpp"
#include <cstdlib>
// Independent RAM reader: the ETR's published map-server configuration must carry policy, never the password.
#include <iostream>
#include <string>
#include "frogram.hpp"
int main(){
 try {
  frogram::Session s(getenv("RIBBIT_RAM_HOST")?getenv("RIBBIT_RAM_HOST"):"127.0.0.1",getenv("RIBBIT_RAM_PORT")?atoi(getenv("RIBBIT_RAM_PORT")):8788,LISPER_API); frogram::Memory m(s,LISPER_API);
  auto cells=m.read("lisp","etr-map-server","192.0.2.251");
  if(cells.size()!=1){std::cerr<<"expected one map-server cell, got "<<cells.size()<<"\n";return 2;}
  auto &b=cells[0].bag;
  if(b["password"].type!=frogram::Json::Null){std::cerr<<"password field leaked\n";return 3;}
  if(b["address"].s!="192.0.2.251"){std::cerr<<"map-server address missing\n";return 4;}
  std::cout<<"ms_secret_absent=PASS\n";
  return 0;
 } catch(const std::exception&x){std::cerr<<x.what()<<"\n";return 1;}
}
