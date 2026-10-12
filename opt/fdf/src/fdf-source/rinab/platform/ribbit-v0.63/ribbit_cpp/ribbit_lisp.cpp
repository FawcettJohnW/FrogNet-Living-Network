/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
#include <set>
#include "lisp_engine.hpp"
#include "version.hpp"
int main(int argc,char**argv){
    if(argc==2&&std::string(argv[1])=="--version"){ std::cout<<"ribbit-lisp "<<RIBBIT_LISP_VERSION<<"\n"; return 0; }
    std::unique_ptr<Engine> ep;
    if(argc==4&&std::string(argv[1])=="--ram"){
        try{ ep.reset(new Engine(argv[2],std::stoi(argv[3]))); }
        catch(const frogram::Unreachable&x){                      // [FAIL_ONCE_LOUDLY_V1] at start too
            std::cerr<<"ribbit-lisp: RAM host "<<argv[2]<<":"<<argv[3]<<" unreachable: "<<x.what()<<"\n"; return 3; }
    }
    else if(argc==1)ep.reset(new Engine());
    else{
        std::cerr<<"usage: ribbit-lisp [--ram HOST PORT]\n";
        return 2;
    }
    Engine&e=*ep;
    // [VENDOR_API_V1] With a RAM host, the Map-Server-side mutating operations are ONE call each to the LISP region's
    // API (op=lisp): the host runs them against its memory. Reads and waits stay here, on held views (no round trip).
    // ETR-side operations stay here too: they build packets with keys that are this participant's own, and a database
    // mapping is the ETR's own truth under its own identity (etr.identity) -- the host would publish it under none.
    static const std::set<std::string> HOSTED={"site.add","site.delete","ms.encryption_key","registration.put","registration.delete",
        "map_cache.add","map_cache.delete","ddt.add","ddt.delete",
        "map_resolver.add","map_resolver.delete","wire.register4","wire.register4_notify","wire.register_notify","wire.register6"};
    const bool remote=argc==4;
    std::string line;
    while(std::getline(std::cin,line)){
        try{
            Json r=Json::parse(line);
            auto op=r["operation"].s;
            std::string result;
            if(op=="version"){ result=std::string("{\"client\":\"")+RIBBIT_LISP_VERSION+"\",\"ram_host\":"+q(e.ram_version())+"}"; }
            else if(remote&&HOSTED.count(op)){
                // the arguments exactly as the caller wrote them: {"operation":...,"args":{...}}
                auto k=line.find("\"args\""),c=line.find(':',k),end=line.rfind('}');
                if(k==std::string::npos||c==std::string::npos||end==std::string::npos||end<=c)throw std::runtime_error("expected {\"operation\",\"args\":{...}}");
                result=e.remote_call(op,line.substr(c+1,end-c-1));
            } else result=e.call(op,r["args"]);
            std::cout<<"{\"ok\":true,\"result\":"<<result<<"}\n"<<std::flush;
        } catch(const frogram::Unreachable&x){
            // [FAIL_ONCE_LOUDLY_V1] the RAM host is gone (or one of our views stopped): say so ONCE, with the cause and
            // the endpoint, and exit -- answering later requests from views that no longer follow the memory would
            // be wrong answers, not errors
            std::string where=argc==4?std::string(argv[2])+":"+argv[3]:std::string("(in-process memory)");
            std::cout<<"{\"ok\":false,\"error\":"<<q(std::string("RAM host ")+where+" unreachable: "+x.what())<<"}\n"<<std::flush;
            std::cerr<<"ribbit-lisp: RAM host "<<where<<" unreachable: "<<x.what()<<" -- exiting\n";
            return 3;
        } catch(const std::exception&x){
            std::cout<<"{\"ok\":false,\"error\":"<<q(x.what())<<"}\n"<<std::flush;
        }
    }
}
