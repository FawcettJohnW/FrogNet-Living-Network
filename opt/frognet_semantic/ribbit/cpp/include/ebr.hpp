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
#pragma once
#include <atomic>
#include <cstdint>

namespace ribbit {
// [NOBODY_IS_ASKED_V1] (John, 2026-11-25, when four 4,000-ticket races in a row each ran slower than the last: "Why is
// it walking the threads. That is messaging style." "There should be no walks. No acks, no copying things around.")
// This class kept a record for every thread that ever took a guard, freed only with the Ebr, and before freeing
// anything went down all of them asking each "are you in?" -- twice per finished call. A session used by 4,000 new
// threads a race kept 4,000 dead records a race: the seven claimants' CPU went 10.1 s, 14.9 s, 20.0 s, 31.4 s over
// four races with the RAM host's flat at 2.7 s.
//
// Now there is no record of a thread and nothing to walk. Every guard writes its own truth into the one place
// everybody reads: it counts itself into the current epoch when taken and out of that epoch when left. There are
// three epochs' worth of counts and of retired lists, used in turn.
//
//   taking a guard   read the epoch, count into it, read the epoch again; not the same: count out and take it again.
//   retiring         done under a guard: onto the list of the epoch as it is then.
//   the epoch moves  from e to e+1 when nobody is counted into e-1. Whoever moves it is itself counted into e, so it
//                    cannot move again under them, and frees the list of e-1: what is on it was unlinked before
//                    epoch e began, and nobody who entered before then is still in.
//   who moves it     whoever notices: a retire, and the guard that is the last out of an epoch that has ended.
// A pointer is on exactly one list from retire to free. Nothing is handed from one thread to another.
class Ebr {
    struct Retired{
        const void*ptr;
        void(*destroy)(const void*);
        Retired*next;
    };
    struct alignas(64) Of{                       // one epoch in three: who is in it, what was retired in it
        std::atomic<int64_t>in{0};
        std::atomic<Retired*>retired{nullptr};
    };
    std::atomic<uint64_t>epoch_{3};
    Of of_[3];
    uint64_t enter(){
        for(;;){
            const uint64_t e=epoch_.load(std::memory_order_seq_cst);
            of_[e%3].in.fetch_add(1,std::memory_order_seq_cst);
            if(epoch_.load(std::memory_order_seq_cst)==e)return e;
            of_[e%3].in.fetch_sub(1,std::memory_order_seq_cst);
        }
    }
    bool something_retired()const{
        return of_[0].retired.load(std::memory_order_acquire)||of_[1].retired.load(std::memory_order_acquire)
             ||of_[2].retired.load(std::memory_order_acquire);
    }
    static void free_all(Retired*list){while(list){Retired*n=list;list=list->next;n->destroy(n->ptr);delete n;}}
    void leave(uint64_t e){                      // the last out of an epoch that has ended lets the epoch move
        if(of_[e%3].in.fetch_sub(1,std::memory_order_seq_cst)==1
           &&epoch_.load(std::memory_order_seq_cst)!=e&&something_retired())move();
    }
    void move(){
        for(;;){
            const uint64_t e=enter();bool moved=false;
            if(of_[(e+2)%3].in.load(std::memory_order_seq_cst)==0){          // nobody is left in e-1
                uint64_t was=e;
                if(epoch_.compare_exchange_strong(was,e+1,std::memory_order_seq_cst)){
                    moved=true;
                    free_all(of_[(e+2)%3].retired.exchange(nullptr,std::memory_order_acq_rel));
                }
            }
            const bool last=of_[e%3].in.fetch_sub(1,std::memory_order_seq_cst)==1;
            if(!(moved&&last&&something_retired()))return;                     // someone still in will notice when it leaves
        }
    }
public:
    class Guard{
        Ebr*e_=nullptr;uint64_t at_=0;
    public:
        explicit Guard(Ebr&e):e_(&e),at_(e.enter()){}
        Guard(const Guard&)=delete;
        Guard&operator=(const Guard&)=delete;
        Guard(Guard&&o)noexcept:e_(o.e_),at_(o.at_){o.e_=nullptr;}
        ~Guard(){if(e_)e_->leave(at_);}
        template<class T>const T*protect(const std::atomic<const T*>&slot){
            return slot.load(std::memory_order_seq_cst);
        }
    };
    Guard guard(){return Guard(*this);}
    template<class T>void retire(const T*p){
        if(!p)return;
        auto*n=new Retired{p,[](const void*q){delete static_cast<const T*>(q);},nullptr};
        const uint64_t mine=enter();
        std::atomic<Retired*>&list=of_[epoch_.load(std::memory_order_seq_cst)%3].retired;
        Retired*head=list.load(std::memory_order_relaxed);
        do{n->next=head;}while(!list.compare_exchange_weak(head,n,std::memory_order_release,std::memory_order_relaxed));
        of_[mine%3].in.fetch_sub(1,std::memory_order_seq_cst);
        move();
    }
    void collect(){if(something_retired())move();}
    void drain(){while(something_retired())move();}   // for a caller that knows no guard is held
    Ebr()=default;
    Ebr(const Ebr&)=delete; Ebr&operator=(const Ebr&)=delete;
    ~Ebr(){for(auto&o:of_)free_all(o.retired.exchange(nullptr));}   // no guard is held any more
};
}
