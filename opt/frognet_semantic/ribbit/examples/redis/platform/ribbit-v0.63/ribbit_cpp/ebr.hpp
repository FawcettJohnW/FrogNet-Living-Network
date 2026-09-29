#pragma once
#include <atomic>
#include <cstdint>

namespace ribbit {
class Ebr {
    struct Reader{
        std::atomic<bool> active{false};
        Reader*next=nullptr;
        unsigned depth=0;
    };
    struct Retired{
        const void*ptr;
        void(*destroy)(const void*);
        Retired*next;
    };
    std::atomic<Reader*>readers_{nullptr};
    std::atomic<Retired*>retired_{nullptr};
    Reader*register_reader(){
        auto*r=new Reader;
        Reader*head=readers_.load(std::memory_order_relaxed);
        do{r->next=head;}while(!readers_.compare_exchange_weak(
            head,r,std::memory_order_release,std::memory_order_relaxed));
        return r;
    }
    // [EBR_OWNED_READERS_V1] A thread's reader record belongs to the Ebr it was made for, and is freed with it (they used
    // to live forever: every short-lived Ebr -- the RAM server makes a resident Engine, with its own Ebr, per operation --
    // left a record behind, about 35 bytes per register, on a server that stays up). A thread remembers WHICH Ebr its
    // record is for by that Ebr's serial, never by its address: a later Ebr at a reused address is a different one.
    static uint64_t next_serial(){ static std::atomic<uint64_t> n{0}; return ++n; }
    const uint64_t serial_=next_serial();
    Reader*local_reader(){
        struct Local{uint64_t owner=0;Reader*reader=nullptr;};
        thread_local Local local;
        if(local.owner!=serial_){local.owner=serial_;local.reader=register_reader();}
        return local.reader;
    }
    void push_retired(Retired*n){
        Retired*head=retired_.load(std::memory_order_relaxed);
        do{n->next=head;}while(!retired_.compare_exchange_weak(
            head,n,std::memory_order_release,std::memory_order_relaxed));
    }
    bool quiescent()const{
        for(Reader*r=readers_.load(std::memory_order_acquire);r;r=r->next)
            if(r->active.load(std::memory_order_seq_cst))return false;
        return true;
    }
public:
    class Guard{
        Reader*reader_=nullptr;
    public:
        explicit Guard(Ebr&e):reader_(e.local_reader()){
            if(reader_->depth++==0)reader_->active.store(true,std::memory_order_seq_cst);
        }
        Guard(const Guard&)=delete;
        Guard&operator=(const Guard&)=delete;
        Guard(Guard&&o)noexcept:reader_(o.reader_){o.reader_=nullptr;}
        ~Guard(){
            if(reader_&&--reader_->depth==0)
                reader_->active.store(false,std::memory_order_seq_cst);
        }
        template<class T>const T*protect(const std::atomic<const T*>&slot){
            return slot.load(std::memory_order_seq_cst);
        }
    };
    Guard guard(){return Guard(*this);}
    template<class T>void retire(const T*p){
        if(!p)return;
        auto*n=new Retired{p,[](const void*q){delete static_cast<const T*>(q);},nullptr};
        push_retired(n);collect();
    }
    void collect(){
        if(!quiescent())return;
        Retired*list=retired_.exchange(nullptr,std::memory_order_acq_rel);
        if(!list)return;
        if(!quiescent()){
            while(list){Retired*n=list;list=list->next;push_retired(n);}
            return;
        }
        while(list){Retired*n=list;list=list->next;n->destroy(n->ptr);delete n;}
    }
    void drain(){while(retired_.load(std::memory_order_acquire))collect();}
    Ebr()=default;
    Ebr(const Ebr&)=delete; Ebr&operator=(const Ebr&)=delete;
    ~Ebr(){                                   // no reader is active any more: free what was retired, then the readers
        for(Retired*n=retired_.exchange(nullptr);n;){Retired*x=n->next;n->destroy(n->ptr);delete n;n=x;}
        for(Reader*r=readers_.exchange(nullptr);r;){Reader*x=r->next;delete r;r=x;}
    }
};
}
