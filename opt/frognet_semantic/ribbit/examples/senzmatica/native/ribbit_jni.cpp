// Copyright (C) 2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include <jni.h>
#include "ribbit_pp.hpp"
#include "semtpl.hpp"
#include <atomic>
#include <cstdio>
#include <limits>
#include <stdexcept>
using namespace ribbit::pp;
namespace {
struct Connection {RibbitConnection session;unsigned watches=0;std::atomic<unsigned> failures{0};};
Connection& connection(jlong handle){if(!handle)throw std::logic_error("Ribbit is disconnected");return *reinterpret_cast<Connection*>(handle);}
std::string bytes(JNIEnv* env,jbyteArray value){
    if(!value)throw std::invalid_argument("null native string");
    auto size=env->GetArrayLength(value);std::string result(size,'\0');
    env->GetByteArrayRegion(value,0,size,reinterpret_cast<jbyte*>(result.data()));
    if(env->ExceptionCheck())throw std::runtime_error("cannot read Java array");
    return result;
}
jbyteArray array(JNIEnv* env,const std::string& text){
    if(text.size()>size_t(std::numeric_limits<jsize>::max()))throw std::length_error("JNI result too large");
    auto result=env->NewByteArray(jsize(text.size()));if(!result)throw std::runtime_error("cannot allocate Java result");
    env->SetByteArrayRegion(result,0,jsize(text.size()),reinterpret_cast<const jbyte*>(text.data()));return result;
}
void failure(JNIEnv* env,const std::exception& error){
    if(env->ExceptionCheck())return;
    const char* type=dynamic_cast<const std::invalid_argument*>(&error)?"java/lang/IllegalArgumentException":dynamic_cast<const std::logic_error*>(&error)?"java/lang/IllegalStateException":"java/lang/RuntimeException";
    auto cls=env->FindClass(type);if(cls){env->ThrowNew(cls,error.what());env->DeleteLocalRef(cls);}
}
struct Callback {
    JavaVM* vm;jobject consumer;jmethodID accept;Connection* owner;
    void operator()(const JsonBag& value)const noexcept {
        JNIEnv* env=nullptr;bool attached=false;
        if(vm->GetEnv(reinterpret_cast<void**>(&env),JNI_VERSION_1_8)==JNI_EDETACHED){
            if(vm->AttachCurrentThreadAsDaemon(reinterpret_cast<void**>(&env),nullptr)!=JNI_OK){++owner->failures;std::fprintf(stderr,"Ribbit JNI: attach failed\n");return;}
            attached=true;
        }
        if(!env){++owner->failures;return;}
        try {
            auto text=array(env,semtpl::dumps_ascii(value,false));env->CallVoidMethod(consumer,accept,text);
            if(env->ExceptionCheck()){++owner->failures;env->ExceptionDescribe();env->ExceptionClear();}
            env->DeleteLocalRef(text);
        }catch(const std::exception& error){++owner->failures;std::fprintf(stderr,"Ribbit JNI callback: %s\n",error.what());if(env->ExceptionCheck()){env->ExceptionDescribe();env->ExceptionClear();}}
        if(attached)vm->DetachCurrentThread();
    }
};
}
extern "C" JNIEXPORT jlong JNICALL Java_com_fawcett_ribbit_Ribbit_nConnect(JNIEnv* env,jclass,jbyteArray address,jbyteArray vendor){
    try{auto value=std::make_unique<Connection>();value->session.ConnectRibbit(bytes(env,address),bytes(env,vendor));return reinterpret_cast<jlong>(value.release());}catch(const std::exception& e){failure(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_com_fawcett_ribbit_Ribbit_nDisconnect(JNIEnv* env,jclass,jlong handle){
    try{auto& value=connection(handle);if(value.watches)throw std::logic_error("active watches require a process-lifetime connection");value.session.DisconnectRibbit();delete &value;}catch(const std::exception& e){failure(env,e);}
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_com_fawcett_ribbit_Ribbit_nCall(JNIEnv* env,jclass,jlong handle,jint op,jbyteArray a,jbyteArray b,jbyteArray c,jbyteArray bag){
    try{auto& value=connection(handle);Capability tuples(value.session.GetSpace());const TupleName name{bytes(env,a),bytes(env,b),bytes(env,c)};
        switch(op){case 0:tuples.Set(name,Tuple::ParseBag(bytes(env,bag)));return nullptr;case 1:return array(env,semtpl::dumps_ascii(tuples.Get(name),false));case 2:tuples.SetCapability(name,Tuple::ParseBag(bytes(env,bag)));return nullptr;case 3:return array(env,tuples.CheckCapability(name)?"true":"false");default:throw std::invalid_argument("unknown JNI operation");}
    }catch(const std::exception& e){failure(env,e);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_com_fawcett_ribbit_Ribbit_nWatch(JNIEnv* env,jclass,jlong handle,jbyteArray a,jbyteArray b,jbyteArray c,jobject consumer,jboolean capabilities){
    jobject global=nullptr;
    try{auto& value=connection(handle);if(!consumer)throw std::invalid_argument("callback required");JavaVM* vm=nullptr;if(env->GetJavaVM(&vm)!=JNI_OK)throw std::runtime_error("cannot obtain JVM");
        auto cls=env->GetObjectClass(consumer);if(!cls)throw std::runtime_error("callback class unavailable");auto accept=env->GetMethodID(cls,"accept","(Ljava/lang/Object;)V");env->DeleteLocalRef(cls);if(!accept)throw std::invalid_argument("Consumer required");
        global=env->NewGlobalRef(consumer);if(!global)throw std::runtime_error("cannot retain callback");
        const TupleName name{bytes(env,a),bytes(env,b),bytes(env,c)};const Callback callback{vm,global,accept,&value};Capability tuples(value.session.GetSpace());
        if(capabilities)tuples.WatchCapabilities(name,callback);else tuples.Watch(name,callback);++value.watches;
    }catch(const std::exception& e){if(global)env->DeleteGlobalRef(global);failure(env,e);}
}
// HeldRead on the caller's thread: the read returns what landed past `after` (JSON bag with rows and the new after),
// or an empty batch when the hold expires; a session that has ended throws, so the caller sees it.
extern "C" JNIEXPORT jbyteArray JNICALL Java_com_fawcett_ribbit_Ribbit_nHeld(JNIEnv* env,jclass,jlong handle,jbyteArray a,jbyteArray b,jbyteArray c,jlong after,jdouble wait){
    try{auto& value=connection(handle);Tuple tuples(value.session.GetSpace());const TupleName name{bytes(env,a),bytes(env,b),bytes(env,c)};
        if(after<0){throw std::invalid_argument("held read cursor must be nonnegative");}
        uint64_t cursor=uint64_t(after);
        return array(env,semtpl::dumps_ascii(tuples.HeldRead(name,cursor,double(wait)),false));
    }catch(const std::exception& e){failure(env,e);return nullptr;}
}
extern "C" JNIEXPORT jint JNICALL Java_com_fawcett_ribbit_Ribbit_nFailures(JNIEnv* env,jclass,jlong handle){try{return jint(connection(handle).failures.load());}catch(const std::exception& e){failure(env,e);return -1;}}
