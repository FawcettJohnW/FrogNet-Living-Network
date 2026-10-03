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
#include "ebr.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <memory>
#include <vector>
struct Node { uint64_t value=7; };
template<class F> double run(F f){
    constexpr uint64_t n=5000000;
    volatile uint64_t sum=0;
    auto a=std::chrono::steady_clock::now();
    for(uint64_t i=0;i<n;i++)sum+=f();
    auto b=std::chrono::steady_clock::now();
    if(sum!=n*7)std::abort();
    return std::chrono::duration<double,std::nano>(b-a).count()/n;
}
static double median(std::vector<double> v){std::sort(v.begin(),v.end());return v[v.size()/2];}
int main(){
    std::shared_ptr<const Node> sp=std::make_shared<Node>();
    ribbit::Ebr ebr; std::atomic<const Node*> raw{new Node};
    std::vector<double> s,e;
    for(int i=0;i<9;i++)s.push_back(run([&]{auto p=std::atomic_load(&sp);return p->value;}));
    for(int i=0;i<9;i++)e.push_back(run([&]{auto g=ebr.guard();auto*p=g.protect(raw);return p->value;}));
    std::cout<<std::fixed<<std::setprecision(2);
    std::cout<<"shared_ptr_atomic_median_ns="<<median(s)<<"\n";
    std::cout<<"ebr_raw_pointer_median_ns="<<median(e)<<"\n";
    std::cout<<"ratio_shared_over_ebr="<<median(s)/median(e)<<"\n";
    delete raw.load();
}
