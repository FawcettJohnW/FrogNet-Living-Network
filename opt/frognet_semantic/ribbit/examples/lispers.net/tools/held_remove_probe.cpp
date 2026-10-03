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
#include "version.hpp"
#include "frogram.hpp"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

int main(int argc,char**argv){
    std::string host=argc>1?argv[1]:"127.0.0.1";
    int port=argc>2?std::stoi(argv[2]):8788;
    frogram::Session writer(host,port,LISPER_API), reader(host,port,LISPER_API);
    frogram::Memory w(writer,LISPER_API), r(reader,LISPER_API);
    const std::string service="lisp-held-remove-probe";
    const std::string variable="registration|0||/24";
    const std::string instance="198.18.250.0/24|probe";
    for(auto&c:w.read(service,variable,instance))w.remove(c.id);
    auto id=w.write(service,variable,instance,"{\"active\":true}");
    auto initial=r.read(service,variable,instance);
    if(initial.empty()){std::cerr<<"bootstrap failed\n";return 2;}
    std::atomic<bool> returned{false};
    std::vector<frogram::Cell> result;
    std::thread t([&]{result=r.read(service,variable,instance,id,1,0);returned=true;});
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    w.remove(id);
    t.join();
    if(returned && !result.empty()){
        std::cout<<"held_remove_observable=PASS id="<<result.front().id<<"\n";
        return 0;
    }
    std::cout<<"held_remove_observable=FAIL remove produced no later truth\n";
    return 1;
}
