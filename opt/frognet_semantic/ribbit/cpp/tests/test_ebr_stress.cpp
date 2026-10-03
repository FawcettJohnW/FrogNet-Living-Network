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
#include <atomic>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>
struct Node {
    uint64_t value;
    static std::atomic<uint64_t> born,dead;
    explicit Node(uint64_t v):value(v){++born;}
    ~Node(){++dead;}
};
std::atomic<uint64_t> Node::born{0},Node::dead{0};
int main(){
    ribbit::Ebr ebr;
    std::atomic<const Node*> slot{new Node(0)};
    std::atomic<bool> stop{false},bad{false};
    std::vector<std::thread> readers;
    for(int n=0;n<8;n++) readers.emplace_back([&]{
        uint64_t last=0;
        while(!stop.load(std::memory_order_acquire)){
            auto g=ebr.guard();
            auto* p=g.protect(slot);
            if(p){auto v=p->value;if(v<last)bad=true;last=v;}
        }
    });
    for(uint64_t i=1;i<=100000;i++){
        auto* old=slot.exchange(new Node(i),std::memory_order_acq_rel);
        ebr.retire(old);
    }
    stop=true;
    for(auto&t:readers)t.join();
    delete slot.exchange(nullptr);
    ebr.drain();
    std::cout<<"born="<<Node::born<<" dead="<<Node::dead<<"\n";
    return bad || Node::born!=Node::dead;
}
