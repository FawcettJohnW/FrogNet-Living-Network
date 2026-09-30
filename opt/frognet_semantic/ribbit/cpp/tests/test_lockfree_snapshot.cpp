#include <atomic>
#include <iostream>
int main(){
    std::atomic<const int*> p{nullptr};
    if(!p.is_lock_free()) return 1;
    std::cout << "pointer_atomic_lock_free=YES\n";
}
