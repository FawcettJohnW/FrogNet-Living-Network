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
// Synthetic device only; no SenzMate firmware is supplied upstream.
#include "ribbit_pp.hpp"
#include <mosquitto.h>
#include <atomic>
#include <cstring>
#include <cerrno>
#include <memory>
#include <chrono>
#include <ctime>
#include <charconv>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <thread>
#include <unistd.h>
using namespace ribbit;
using namespace ribbit::pp;
#ifndef DEVICE_BROKEN
#define DEVICE_BROKEN 0
#endif
namespace {
std::atomic<int> actual{30};std::atomic<unsigned> applications{0};std::string output;
// device identity: argv[4], else "test-device-001" (the single-host runners pass three arguments)
std::string device="test-device-001";const std::string space="senz-baseline";
std::string applied_generation(){std::ifstream file(output+".ram-generation");return std::string(std::istreambuf_iterator<char>(file),{});}
void remember_generation(const std::string& generation){
    std::ostringstream suffix;suffix<<getpid()<<"."<<std::this_thread::get_id();
    const auto target=output+".ram-generation",temp=target+".tmp."+suffix.str();
    {std::ofstream file(temp);file<<generation;file.close();if(!file)throw std::runtime_error("cannot persist generation");}
    if(std::rename(temp.c_str(),target.c_str()))throw std::runtime_error("cannot publish generation");
}
int integer(const std::string& text){
    int result=0;const char* begin=text.data();
    if(!text.empty() && text[0]=='+'){++begin;if(begin!=text.data()+text.size() && *begin=='-')throw std::invalid_argument("invalid integer");}
    const auto parsed=std::from_chars(begin,text.data()+text.size(),result);
    if(parsed.ec!=std::errc{} || parsed.ptr!=text.data()+text.size() || begin==text.data()+text.size())throw std::invalid_argument("invalid int32 setting");
    return result;
}
int apply_setting(const std::string& text,const std::string& path){
    const int value=integer(text);
    if(DEVICE_BROKEN==1){actual.store(value);++applications;}
    else{int previous=actual.load();while(previous!=value){if(actual.compare_exchange_weak(previous,value)){++applications;break;}}}
    int readback=actual.load();std::ofstream file(output);file<<readback;file.close();
    std::cout<<"APPLIED path="<<path<<" value="<<readback<<" applications="<<applications.load()<<std::endl;return readback;
}
const pyv::Obj& field(const pyv::Obj& value,const char* key){const auto& d=value.as<pyv::Dict>();auto n=d.find(key);if(n<0)throw std::invalid_argument(key);return d.vals[size_t(n)];}
std::string str(const pyv::Obj& value,const char* key){return field(value,key).as<pyv::Str>().s;}
void connected(mosquitto* mqtt,void*,int rc){if(rc){std::cout<<"MQTT_REFUSED by the broker: "<<mosquitto_connack_string(rc)<<std::endl;return;}std::cout<<"MQTT_UP"<<std::endl;const auto topic="S2D/SA/"+device+"/RM/#";mosquitto_subscribe(mqtt,nullptr,topic.c_str(),1);}
// [MQTT_IS_INDEPENDENT_V1] the RAM path does not depend on MQTT: the broker is only the switch-OFF path and the
// compatibility completion. The device stays up when the broker is unreachable; the MQTT library keeps
// reconnecting every 2 s and each change of state is logged.
void mqtt_down(mosquitto*,void*,int rc){std::cout<<"MQTT_DOWN "<<(rc?mosquitto_strerror(rc):"disconnected")<<" -- reconnecting every 2 s; the RAM path is unaffected"<<std::endl;}
void mqtt_log(mosquitto*,void*,int level,const char* text){if(level&(MOSQ_LOG_ERR|MOSQ_LOG_WARNING))std::cout<<"MQTT_LOG "<<text<<std::endl;}
void mqtt_message(mosquitto* mqtt,void* context,const mosquitto_message* message){
    std::string reply="rm-conf-unsuccessful";
    try{
        const std::string payload(static_cast<const char*>(message->payload),size_t(message->payloadlen));
        if(payload.rfind("RM|1-",0)!=0 || payload.size()<11 || payload.substr(payload.size()-5)!=";|END")throw std::invalid_argument("unsupported configuration");
        const int readback=apply_setting(payload.substr(5,payload.size()-10),"MQTT");reply="rm-conf-successful";
        pyv::Dict current;current.set("value",pyv::Str{std::to_string(readback)});current.set("status",pyv::Str{"current"});
        static_cast<Tuple*>(context)->Set({space,"applied-config|"+device,"1"},current);
    }catch(const std::exception& e){std::cerr<<"MQTT apply/observation failed: "<<e.what()<<std::endl;}
    const std::string topic=message->topic,ack="D2S/SA/"+topic.substr(std::string("S2D/SA/").size());
    auto rc=mosquitto_publish(mqtt,nullptr,ack.c_str(),int(reply.size()),reply.data(),1,false);
    if(rc!=MOSQ_ERR_SUCCESS)std::cerr<<"MQTT reply failed: "<<rc<<std::endl;
}
}
int main(int argc,char**argv){
    if(argc!=4&&argc!=5)return 2;
    if(argc==5)device=argv[4];
    // MQTT broker: argv[2] is PORT (on 127.0.0.1) or HOST:PORT
    const std::string broker=argv[2];const auto colon=broker.rfind(':');
    const std::string mqtt_host=colon==std::string::npos?"127.0.0.1":broker.substr(0,colon);const int mqtt_port=std::stoi(colon==std::string::npos?broker:broker.substr(colon+1));
    output=argv[3];{std::ifstream state(output);int value;if(state>>value)actual=value;}
    // this process's boot: a fresh one after every session end, so a reader can tell boots apart
    const std::string boot=std::to_string(getpid())+"."+std::to_string(time(nullptr));
    // [SAYS_WHERE_IT_FAILED_V1] every start says what it is connecting to, and every failure says at which step,
    // so the journal answers "why is it restarting" without anyone running a command
    std::cout<<"CONNECTING device="<<device<<" ram="<<argv[1]<<" mqtt="<<mqtt_host<<":"<<mqtt_port<<std::endl;
    const auto started=std::chrono::steady_clock::now();
    auto seconds=[&]{return std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();};
    std::unique_ptr<Space> opened;
    try{opened=std::make_unique<Space>(argv[1],"senz-binding");}
    catch(const std::exception& e){
        const std::string why=e.what();
        std::cout<<"NO_SESSION ram="<<argv[1]<<": "<<why<<(why.find("connect failed")!=std::string::npos
            ?" -- nothing accepted a connection there; is 'senz ram' running on that machine and its port reachable?"
            :" -- something accepted the connection and closed it before a session was set up; whatever answers on that port is not serving the RAM (is it 'senz ram', or another program on that port?)")
            <<" Ending for a fresh attempt"<<std::endl;std::cout.flush();_exit(1);}
    Space& session=*opened;Capability tuples(session);
    pyv::Dict capability;capability.set("format",pyv::Str{"integer"});capability.set("parameter",pyv::Str{"Test sampling interval"});capability.set("boot",pyv::Str{boot});tuples.SetCapability({space,"capability|"+device,"1"},capability);
    pyv::Dict initial;initial.set("value",pyv::Str{std::to_string(actual.load())});initial.set("status",pyv::Str{"current"});tuples.Set({space,"applied-config|"+device,"1"},initial);
    try{(void)tuples.Get({space,"capability|"+device,"1"});}   // one round trip: is the far end really a RAM memory?
    catch(const std::exception& e){std::cout<<"NO_SESSION ram="<<argv[1]<<": the connection was accepted but the first read failed ("<<e.what()<<") -- whatever answers on that port is not serving the RAM (is it 'senz ram', or another program on that port?). Ending for a fresh attempt"<<std::endl;std::cout.flush();_exit(1);}
    std::cout<<"SESSION_UP device="<<device<<" ram="<<argv[1]<<" boot="<<boot<<" setting="<<actual.load()<<std::endl;
    auto consume=[&](const JsonBag& result){
        for(const auto& row:field(result,"rows").as<pyv::List>()){
            if(field(row,"gone").as<bool>())continue;
            auto parameter=str(field(row,"name"),"indexC");if(parameter!="1")continue;
            const auto& wanted=field(row,"bag");pyv::Dict report;
            try{
                const auto generation=str(wanted,"generation"),desired=str(wanted,"value");report.set("generation",pyv::Str{generation});
                if(applied_generation()==generation && actual.load()!=integer(desired)){
                    report.set("value",pyv::Str{std::to_string(actual.load())});report.set("status",pyv::Str{"current"});std::cout<<"REOBSERVED completed RAM generation; actual="<<actual.load()<<std::endl;
                }else{auto value=apply_setting(desired,"RAM");remember_generation(generation);report.set("value",pyv::Str{std::to_string(value)});report.set("status",pyv::Str{"applied"});}
            }catch(const std::exception& e){std::cerr<<"RAM apply failed: "<<e.what()<<std::endl;report.set("value",pyv::Str{std::to_string(actual.load())});report.set("status",pyv::Str{"failed"});}
            // the device's own count of physical applications this boot: what a remote checker reads for "applied twice"
            report.set("applications",pyv::Int::of(int64_t(applications.load())));report.set("boot",pyv::Str{boot});
            tuples.Set({space,"applied-config|"+device,parameter},report);
        }
    };
    // [SESSION_END_ENDS_PROCESS_V1] the desired family is a held read this process owns, so the end of its session is
    // seen here: a read that fails means the session is gone, and this process ends for a fresh one -- the same rule as
    // fdf-client ("a failed read ends the session ... the fresh one reads again"). Its supervisor (systemd
    // Restart=always, RestartSec=2: node/systemd/senz-device.service) starts the fresh one, which announces its
    // capability, writes its actual setting and reads the current desired state. No in-process redial.
    std::thread([&]{
        uint64_t after=0;
        try{
            for(;;){auto result=tuples.HeldRead({space,"desired-config|"+device,""},after,25);if(!field(result,"rows").as<pyv::List>().empty())consume(result);}
        }catch(const std::exception& e){
            std::cout<<"SESSION_ENDED after "<<int(seconds())<<" s: "<<e.what()<<" -- ending this process for a fresh one"<<std::endl;std::cout.flush();_exit(1);
        }
    }).detach();
    mosquitto_lib_init();auto* mqtt=mosquitto_new(("senz-"+device).c_str(),true,static_cast<Tuple*>(&tuples));
    if(!mqtt){std::cout<<"NO_MQTT cannot create an MQTT client -- ending"<<std::endl;std::cout.flush();_exit(3);}
    mosquitto_connect_callback_set(mqtt,connected);mosquitto_disconnect_callback_set(mqtt,mqtt_down);mosquitto_log_callback_set(mqtt,mqtt_log);mosquitto_message_callback_set(mqtt,mqtt_message);
    mosquitto_reconnect_delay_set(mqtt,2,2,false);
    if(int rc=mosquitto_connect_async(mqtt,mqtt_host.c_str(),mqtt_port,60);rc!=MOSQ_ERR_SUCCESS)
        std::cout<<"MQTT_DOWN broker "<<mqtt_host<<":"<<mqtt_port<<": "<<(rc==MOSQ_ERR_ERRNO?std::strerror(errno):mosquitto_strerror(rc))<<" -- is 'senz ops' running there and port "<<mqtt_port<<" reachable? Reconnecting every 2 s; the RAM path is unaffected"<<std::endl;
    if(mosquitto_loop_start(mqtt)!=MOSQ_ERR_SUCCESS){std::cout<<"NO_MQTT cannot start the MQTT loop -- ending"<<std::endl;std::cout.flush();_exit(5);}
    std::cout<<"READY"<<std::endl;std::string line;
    bool told_to_stop=false;
    while(std::getline(std::cin,line)){if(line=="reobserve")consume(tuples.Get({space,"desired-config|"+device,""}));else{told_to_stop=true;break;}}
    // no stdin (a service: StandardInput=null) is not a stop: the device runs until its session ends or it is signalled
    if(!told_to_stop)for(;;)pause();
    session.flush(5);std::cout.flush();_exit(0);
}
