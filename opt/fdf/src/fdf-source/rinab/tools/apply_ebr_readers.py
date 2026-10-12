"""apply_ebr_readers.py EBR_HPP -- [EBR_READER_PER_INSTANCE_V1] a thread keeps a reader record for EACH Ebr it uses.

ribbit::Ebr gave each thread ONE remembered reader record (thread_local, tagged with the Ebr's serial). A thread that
alternates between two Ebr instances -- the RAM host's pool threads now use the coalescing table's and the RINA
memory's -- registered a NEW record on every switch, so every instance's reader list grew by one per request, for the
life of the process, and quiescent() (which walks that list on every retire) grew with it. Measured: the host's CPU per
rr run doubled run after run (60 -> 145 -> 243 ticks for three runs of 1,500), Ebr::collect reached 23% of the host.

Now a thread remembers up to 8 (Ebr serial, record) pairs; a ninth Ebr replaces the oldest. A thread using a handful of
instances registers once per instance, as was intended. Applied at build; the vendored v0.63 source stays untouched."""
import sys
p = sys.argv[1]; s = open(p).read()
a = '''    Reader*local_reader(){
        struct Local{uint64_t owner=0;Reader*reader=nullptr;};
        thread_local Local local;
        if(local.owner!=serial_){local.owner=serial_;local.reader=register_reader();}
        return local.reader;
    }'''
b = '''    Reader*local_reader(){                       // [EBR_READER_PER_INSTANCE_V1] one record per (thread, Ebr)
        struct Local{uint64_t owner=0;Reader*reader=nullptr;};
        thread_local Local local[8]; thread_local unsigned next_victim=0;
        for(auto&l:local) if(l.owner==serial_) return l.reader;
        Local&l=local[next_victim++%8]; l.owner=serial_; l.reader=register_reader();
        return l.reader;
    }'''
assert s.count(a) == 1, s.count(a); s = s.replace(a, b)
open(p, 'w').write(s); print("ebr readers applied")
