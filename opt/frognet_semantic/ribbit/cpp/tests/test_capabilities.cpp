// Copyright (C) 2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "ribbit_pp.hpp"
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <thread>
#include <unistd.h>
using namespace ribbit;
using namespace ribbit::pp;
static int failures = 0;
static void check(bool ok, const char* label) {
    printf("%s %s\n", ok ? "PASS" : "FAIL", label); fflush(stdout); if (!ok) ++failures;
}
static const pyv::Obj& field(const pyv::Obj& object, const char* key) {
    const auto& d = object.as<pyv::Dict>(); return d.vals.at(size_t(d.find(key)));
}
static const pyv::List& rows(const JsonBag& bag) { return field(bag, "rows").as<pyv::List>(); }
static int64_t id(const JsonBag& bag) { return field(bag, "after").as<pyv::Int>().v; }
static JsonBag bag(const std::string& text) { return Tuple::ParseBag(text); }
template<class F> static bool rejects(F action) {
    try { action(); } catch (const std::invalid_argument&) { return true; } return false;
}
// Test-only synchronization. The library keeps no mutable capability view.
struct Seen {
    std::mutex mutex; std::condition_variable cv;
    int live = 0, inactive = 0, gone = 0, last_v = 0;
    void add(const JsonBag& result) {
        { std::lock_guard<std::mutex> lock(mutex);
          for (const auto& row : rows(result)) {
            if (field(row, "gone").as<bool>()) ++gone;
            else {
                const auto& value = field(row, "bag").as<pyv::Dict>();
                const auto n = value.find("_capability_active");
                if (n >= 0 && value.vals[size_t(n)].is<bool>() && !value.vals[size_t(n)].as<bool>()) ++inactive;
                else ++live;
                const auto version = value.find("v");
                if (version >= 0 && value.vals[size_t(version)].is<pyv::Int>())
                    last_v = int(value.vals[size_t(version)].as<pyv::Int>().v);
            }
          } }
        cv.notify_all();
    }
    template<class F> bool until(F predicate) {
        std::unique_lock<std::mutex> lock(mutex);
        return cv.wait_for(lock, std::chrono::seconds(5), predicate);
    }
};
static void snapshot(Space& writer, Space& reader, const std::string& service) {
    Capability cap(writer), view(reader); Tuple tuples(reader);
    const TupleName a{service,"resize","A"}, b{service,"resize","B"}, family{service,"resize",""};
    check(!view.CheckCapability(a) && rows(tuples.Get(a)).empty(), "absent tuple is an empty result, capability false");
    cap.SetCapability(a, bag("{\"width\":640,\"codec\":\"h264\"}"));
    cap.SetCapability(b, bag("{\"width\":1280}")); cap.SetCapability({service,"print","A"}, bag("{}"));
    writer.flush(5); uint64_t cursor = 0; auto landed = tuples.HeldRead(b, cursor, 5);
    check(rows(landed).size() == 1 && cursor > 0, "held read uses TupleName and returns JSON bag with cursor");
    check(view.CheckCapability(a) && view.CheckCapability(family) && !view.CheckCapability({service,"resize","C"}) &&
          !view.CheckCapability({service,"res",""}), "exact tuples and tuple families retain independent coordinates");
    auto got = view.GetCapability(a);
    check(field(field(rows(got).at(0),"bag"),"width").as<pyv::Int>().v == 640,
          "get delegates to base read and returns JSON metadata");
    check(rows(view.EnumerateCapabilities({service,"",""})).size() == 3 &&
          rows(view.EnumerateCapabilities(family)).size() == 2 &&
          rows(view.EnumerateCapabilities({service,"","A"})).size() == 2,
          "enumerate whole space, family, and independent instance selector");
    cursor = uint64_t(id(got)); cap.SetCapability(a, bag("{\"width\":1920}")); writer.flush(5);
    tuples.HeldRead(a,cursor,5);
    check(field(field(rows(view.GetCapability(a)).at(0),"bag"),"width").as<pyv::Int>().v == 1920,
          "set updates exact tuple");
    cap.WithdrawCapability(a); writer.flush(5); tuples.HeldRead(a,cursor,5);
    check(!view.CheckCapability(a) && view.CheckCapability(b) && view.CheckCapability(family),
          "withdraw changes only the named tuple");
    check(rows(view.GetCapability(a)).size() == 1 && rows(view.EnumerateCapabilities(family)).size() == 1 &&
          rows(view.EnumerateCapabilities(family,true)).size() == 2,
          "withdrawn truth readable and excluded from active enumeration");
    check(!view.CheckCapability({service+".other","resize","A"}), "spaces are isolated");
    const TupleName hostile{service,"dots/slash.\x1f", "quote\"/A"};
    cap.SetCapability(hostile,bag("{}")); writer.flush(5); uint64_t h=0;tuples.HeldRead(hostile,h,5);
    check(view.CheckCapability(hostile) && !view.CheckCapability({service,"dots/slash.","quote\"/A"}),
          "delimiters in coordinates never alias concatenated names");
    check(rejects([&]{ cap.Set(a,pyv::List{}); }) && rejects([&]{ Tuple::ParseBag("[]"); }) &&
          rejects([&]{ Tuple::ParseBag("{broken"); }) && rejects([&]{ Tuple::ParseBag("null"); }),
          "nonobject and malformed bags rejected");
    pyv::Dict nonfinite; nonfinite.set("bad", NAN);
    pyv::Dict bytes; bytes.set("bad",pyv::Bytes{"x"});
    check(rejects([&]{ cap.Set(a,nonfinite); }) && rejects([&]{ cap.Set(a,bytes); }) &&
          rejects([&]{ cap.Set({service,"resize",""},bag("{}")); }) &&
          rejects([&]{ tuples.Get({"","",""}); }) &&
          rejects([&]{ cap.Set({service,"resize",std::string("A\0B",3)},bag("{}")); }),
          "nonJSON values, family writes, empty spaces and NUL coordinates rejected");
    check(rejects([&]{ cap.SetCapability(a,bag("{\"_capability_active\":true}")); }),
          "capability metadata cannot forge reserved active state");
    const TupleName bad{service,"malformed","A"};
    cap.Set(bad,bag("{\"_capability_active\":\"false\"}")); writer.flush(5);uint64_t z=0;tuples.HeldRead(bad,z,5);
    check(!view.CheckCapability(bad) && rows(view.EnumerateCapabilities(bad,true)).empty(),
          "malformed foreign capability state never counts as available");
    uint64_t timeout_cursor = uint64_t(id(tuples.Get(b)));
    check(rows(tuples.HeldRead(b,timeout_cursor,0.02)).empty() &&
          rejects([&]{tuples.HeldRead(b,timeout_cursor,-1);}) &&
          rejects([&]{tuples.HeldRead(b,timeout_cursor,NAN);}), "held timeout empty; invalid waits rejected");
    check(rows(view.Get({service,"print","A"})).size()==1,
          "present empty JSON bag stays distinct from absence");
    // Full integer precision of the native JSON model must survive the wrapper.
    const auto big = bag("{\"n\":9007199254740993}");
    const TupleName large{service,"numbers","A"};cap.Set(large,big);writer.flush(5);uint64_t q=0;tuples.HeldRead(large,q,5);
    check(field(field(rows(tuples.Get(large)).at(0),"bag"),"n").as<pyv::Int>() == field(big,"n").as<pyv::Int>(),
          "JSON integer precision survives set/get");
    check(rejects([&]{ Tuple::ParseBag("{\"n\":123456789012345678901234567890}"); }) &&
          rejects([&]{ cap.Set({std::string(65536, 'x'),"v","i"},bag("{}")); }),
          "oversized integers and names fail before reaching asynchronous sender");
}
static void helpers(Space& writer, Space& reader, const std::string& service) {
    Tuple producer(writer), consumer(reader); Capability cap(writer);
    const TupleName first{service,"status","one"}, second{service,"status","two"}, family{service,"status",""};
    check(!consumer.Exists(first) && !consumer.Exists(family), "Exists: absent tuple and family are false");
    producer.SetMany({{first,bag("{\"ready\":false}")}, {second,bag("{\"ready\":true}")}});
    writer.flush(5); uint64_t cursor=0; consumer.HeldRead(second,cursor,5);
    check(consumer.Exists(first) && consumer.Exists(family) && rows(consumer.Get(family)).size()==2,
          "SetMany: independent writes appear; Exists accepts tuple/family");
    auto ready=[](const JsonBag& result) {
        return !rows(result).empty() && field(field(rows(result).front(),"bag"),"ready").as<bool>();
    };
    auto immediate=consumer.WaitUntil(second,ready,0);
    check(field(immediate,"matched").as<bool>() && !field(immediate,"timed_out").as<bool>(),
          "WaitUntil: existing match succeeds even with zero wait");
    int evaluated=0;
    auto changed=consumer.WaitUntil(family,[&](const JsonBag& result) {
        ++evaluated;
        bool complete=rows(result).size()==2;
        for(const auto& row:rows(result)) complete=complete && field(field(row,"bag"),"ready").as<bool>();
        if(!complete && evaluated==1) { // write in the snapshot-to-hold window: must not be missed
            producer.Set(first,bag("{\"ready\":true}")); writer.flush(5);
        }
        return complete;
    },3);
    check(field(changed,"matched").as<bool>() && rows(changed).size()==2 && evaluated>=2,
          "WaitUntil: evaluates full current family and misses no snapshot-to-hold change");
    const TupleName absent{service,"never","none"};
    auto expired=consumer.WaitUntil(absent,[](const JsonBag& result){return !rows(result).empty();},0.02);
    check(!field(expired,"matched").as<bool>() && field(expired,"timed_out").as<bool>() && rows(expired).empty(),
          "WaitUntil: timeout is explicit and never fabricates a match");
    check(rejects([&]{consumer.WaitUntil(first,{},1);}) &&
          rejects([&]{consumer.WaitUntil(first,ready,-1);}) &&
          rejects([&]{consumer.WaitUntil(first,ready,NAN);}), "WaitUntil: invalid predicate/timeouts rejected");
    bool propagated=false;
    try { consumer.WaitUntil(first,[](const JsonBag&)->bool{throw std::logic_error("predicate failed");},1); }
    catch(const std::logic_error&) {propagated=true;}
    check(propagated,"WaitUntil: predicate exceptions propagate");
    const TupleName untouched{service,"batch","first"};
    check(rejects([&]{producer.SetMany({{untouched,bag("{}")},{{service,"batch",""},bag("{}")}});}) &&
          !consumer.Exists(untouched), "SetMany: invalid later name rejects before any write");
    check(rejects([&]{producer.SetMany({{untouched,bag("{}")},{{service,"batch","bad"},pyv::List{}}});}) &&
          !consumer.Exists(untouched), "SetMany: invalid later bag rejects before any write");
    producer.SetMany({});
    producer.SetMany({{first,bag("{\"ready\":false}")},{first,bag("{\"ready\":true}")}}); writer.flush(5);
    check(ready(consumer.Get(first)), "SetMany: empty batch allowed; repeated exact name preserves write order");
    cap.WithdrawCapability(second);writer.flush(5);uint64_t after=cursor;consumer.HeldRead(second,after,5);
    Capability view(reader);
    check(consumer.Exists(second) && !view.CheckCapability(second),
          "Exists means present; explicit withdrawal is not absence");
}

static void watched(const std::string& at, Space& reader) {
    auto* seen = new Seen;
    auto producer = std::make_unique<Space>(at,"capabilities");
    Capability cap(*producer), view(reader); Tuple tuples(reader);
    const TupleName name{"watch","resize","A"};
    cap.SetCapability(name,bag("{}")); producer->flush(5);uint64_t p=0;tuples.HeldRead(name,p,5);
    view.WatchCapabilities(name,[seen](const JsonBag& result){seen->add(result);});
    check(seen->until([&]{return seen->live==1;}),"watch includes existing tuple");
    cap.SetCapability(name,bag("{\"v\":2}"));producer->flush(5);
    check(seen->until([&]{return seen->live==2;}),"held watch reports update");
    cap.WithdrawCapability(name);producer->flush(5);
    check(seen->until([&]{return seen->inactive==1;}),"held watch reports withdrawal");
    cap.SetCapability(name,bag("{}"));producer->flush(5);
    check(seen->until([&]{return seen->live==3;}),"withdrawn tuple can be reasserted");
    cap.SetCapability({"watch","resize","B"},bag("{}"));producer->flush(5);
    // An observed write on A is a barrier after B on the same producer session.
    cap.SetCapability(name,bag("{\"v\":3}"));producer->flush(5);
    const bool reached = seen->until([&]{return seen->last_v==3;});
    { std::lock_guard<std::mutex> lock(seen->mutex);
      check(reached && seen->live==4,"exact watch excludes other instance"); }
    producer.reset();
    check(seen->until([&]{return seen->gone==1;}) && !view.CheckCapability(name),
          "session loss reports gone and removes current capability");
}
static void connections(const std::string& at, Space& observer) {
    RibbitConnection connection;
    auto disconnected=[](RibbitConnection& c) {
        try { (void)c.GetSpace(); } catch (const std::logic_error&) { return true; }
        return false;
    };
    check(!connection.IsConnected() && disconnected(connection), "connection: initial disconnected access rejected");
    connection.DisconnectRibbit(); connection.DisconnectRibbit();
    check(!connection.IsConnected(), "connection: repeated disconnect is harmless");
    check(rejects([&]{connection.ConnectRibbit("", "capabilities");}) &&
          rejects([&]{connection.ConnectRibbit(at, "");}), "connection: empty address/vendor rejected");
    bool failed=false;
    try { connection.ConnectRibbit("invalid-address", "capabilities"); }
    catch(const std::runtime_error&) {failed=true;}
    check(failed && !connection.IsConnected() && disconnected(connection), "connection: failed connect leaves disconnected state");
    connection.ConnectRibbit(at,"capabilities");
    check(connection.IsConnected() && !connection.GetSpace().local(), "connection: remote Space session opens");
    bool twice=false;
    try {connection.ConnectRibbit("here","capabilities");} catch(const std::logic_error&) {twice=true;}
    check(twice && connection.GetSpace().where()==at, "connection: duplicate connect preserves original session");
    Tuple view(observer); const TupleName name{"connection","status","device"}; uint64_t cursor=0;
    {
        Tuple writer(connection.GetSpace()); writer.Set(name,bag("{\"ready\":true}"));
        connection.GetSpace().flush(5); view.HeldRead(name,cursor,5);
        check(view.Exists(name), "connection: tuple API uses owned session");
    }
    connection.DisconnectRibbit();
    check(!connection.IsConnected() && disconnected(connection), "connection: disconnected access rejected after close");
    auto gone=view.WaitUntil(name,[](const JsonBag& r){return rows(r).empty();},5);
    check(field(gone,"matched").as<bool>(), "connection: disconnect removes session-owned writes");
    connection.ConnectRibbit(at,"capabilities");
    check(connection.IsConnected(), "connection: reconnect opens a fresh session");
    connection.DisconnectRibbit();
    connection.ConnectRibbit("here","capabilities");
    check(connection.GetSpace().local(), "connection: local Space mode supported");
    connection.DisconnectRibbit();
}
int main(int argc,char**argv) {
    const std::string port=argc>1?argv[1]:"8959";Space host("here:127.0.0.1:"+port,"capabilities");
    const auto at="127.0.0.1:"+port;
    auto* reader=new Space(at,"capabilities");auto* writer=new Space(at,"capabilities");
    connections(at,*reader);
    snapshot(host,host,"local");snapshot(*writer,*reader,"remote");
    helpers(host,host,"helper.local");helpers(*writer,*reader,"helper.remote");watched(at,*reader);
    printf("RESULT %s capability API (%d failed)\n",failures?"FAIL":"PASS",failures);fflush(stdout);
    _exit(failures?1:0);
}
