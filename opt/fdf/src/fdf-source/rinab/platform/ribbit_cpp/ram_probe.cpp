#include "frogram.hpp"
#include <chrono>
#include <future>
#include <iostream>
#include <thread>
using namespace frogram;
int main(int argc,char**argv){
 if(argc!=3){std::cerr<<"usage: ram-probe HOST PORT\n";return 2;}
 Session a(argv[1],std::stoi(argv[2])), b(argv[1],std::stoi(argv[2])); Memory ma(a),mb(b);
 auto id1=ma.write("lisp-test","held","cell","{\"value\":1}");
 auto fut=std::async(std::launch::async,[&]{auto t=std::chrono::steady_clock::now();auto rows=mb.read("lisp-test","held","cell",(int64_t)id1,3.0);auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t).count();return std::make_pair(rows,ms);});
 std::this_thread::sleep_for(std::chrono::milliseconds(150)); auto id2=ma.write("lisp-test","held","cell","{\"value\":2}"); auto got=fut.get();
 if(got.first.size()!=1 || got.first[0].id!=id2 || got.first[0].bag["value"].n!=2){std::cerr<<"held read contract failed\n";return 1;}
 // Repeat an identical read so FNW1 can use request/reply semantic state.
 mb.read("lisp-test","held","cell"); mb.read("lisp-test","held","cell");
 auto sa=a.stats(), sb=b.stats();
 std::cout<<"held_read_wakeup_ms="<<got.second<<"\n";
 std::cout<<"writer raw="<<sa.raw<<" repeat="<<sa.repeat<<" same="<<sa.same<<" bytes_out="<<sa.bytes_out<<" bytes_in="<<sa.bytes_in<<"\n";
 std::cout<<"reader raw="<<sb.raw<<" repeat="<<sb.repeat<<" same="<<sb.same<<" bytes_out="<<sb.bytes_out<<" bytes_in="<<sb.bytes_in<<"\n";
 ma.remove(id2); return 0;
}
