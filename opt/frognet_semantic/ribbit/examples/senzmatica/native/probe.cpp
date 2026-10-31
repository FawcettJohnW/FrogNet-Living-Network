#include "ribbit_pp.hpp"
#include "semtpl.hpp"
#include <iostream>
#include <memory>
using namespace ribbit::pp;
const pyv::Obj& field(const pyv::Obj& value,const char* key){const auto& d=value.as<pyv::Dict>();auto n=d.find(key);if(n<0)throw std::invalid_argument(key);return d.vals[size_t(n)];}
bool has(const pyv::Obj& value,const char* key){return value.as<pyv::Dict>().find(key)>=0;}
// A line-oriented RAM session for test tools: one JSON command per line in, one JSON result per line out.
//   {"op":"get",   "a","b","c"}                      current rows
//   {"op":"absent","a","b","c"}                      wait up to 5 s for the family to be empty
//   {"op":"held",  "a","b","c","after":N,"wait":S}   held read past N (0 < S <= 30): rows that landed, or none at expiry
//   {"op":"set",   "a","b","c","bag":{...}}          write this session's own tuple
// A failed read prints {"error":...} and the session ends with exit code 1 (the caller takes a fresh one).
int main(int argc,char**argv){
    if(argc!=2)return 2;
    std::unique_ptr<ribbit::Space> opened;
    try{opened=std::make_unique<ribbit::Space>(argv[1],"senz-binding");}
    catch(const std::exception& e){std::cerr<<"probe: cannot open a session to "<<argv[1]<<": "<<e.what()<<std::endl;return 1;}
    ribbit::Space& session=*opened;Tuple tuples(session);std::cout<<"READY"<<std::endl;std::string line;
    while(std::getline(std::cin,line)){
        try{
            auto command=Tuple::ParseBag(line);TupleName name{field(command,"a").as<pyv::Str>().s,field(command,"b").as<pyv::Str>().s,field(command,"c").as<pyv::Str>().s};
            auto op=field(command,"op").as<pyv::Str>().s;JsonBag result;
            if(op=="absent")result=tuples.WaitUntil(name,[](const JsonBag& s){return field(s,"rows").as<pyv::List>().empty();},5);
            else if(op=="held"){uint64_t after=uint64_t(field(command,"after").as<pyv::Int>().v);double wait=has(command,"wait")?(field(command,"wait").is<double>()?field(command,"wait").as<double>():double(field(command,"wait").as<pyv::Int>().v)):25;result=tuples.HeldRead(name,after,wait);}
            else if(op=="set"){tuples.Set(name,field(command,"bag"));pyv::Dict ok;ok.set("ok",true);result=ok;}
            else result=tuples.Get(name);
            std::cout<<semtpl::dumps_ascii(result,false)<<std::endl;
        }catch(const std::exception& e){
            pyv::Dict error;error.set("error",pyv::Str{e.what()});std::cout<<semtpl::dumps_ascii(error,false)<<std::endl;
            std::cout.flush();session.flush(1);_exit(1);
        }
    }
}
