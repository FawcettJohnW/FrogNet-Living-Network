// fdf_up.hpp -- [EVENTS_NOT_TIMERS_V1] a RAM manager says it is up: $FDF_VAR/ram/<host>_<port>.up, written (whole, by a
// rename) when the RAM manager listens, and by this machine's member when its session to that RAM comes up. A program
// whose read failed because the RAM went away waits on that file (fdf_watch.wait_up), not on a timer.
#pragma once
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#include <process.h>
#define FDF_UP_MKDIR(d) _mkdir(d)
#define getpid _getpid
#else
#include <unistd.h>
#define FDF_UP_MKDIR(d) mkdir(d, 0755)
#endif
inline void fdf_say_up(const std::string& hostport, const char* who) {
    const char* v = getenv("FDF_VAR"); const std::string var = v && *v ? v : "/opt/fdf/var", dir = var + "/ram";
    FDF_UP_MKDIR(var.c_str()); FDF_UP_MKDIR(dir.c_str());
    std::string name = hostport; const size_t c = name.rfind(':'); if (c != std::string::npos) name[c] = '_';
    const std::string f = dir + "/" + name + ".up", t = f + "." + std::to_string(getpid()) + ".tmp";
    if (FILE* o = fopen(t.c_str(), "w")) { fprintf(o, "%s pid %d at %ld\n", who, int(getpid()), long(time(nullptr))); fclose(o);
#ifndef _WIN32
                                           chmod(t.c_str(), 0644);
#else
                                           remove(f.c_str());          // Windows: rename does not replace
#endif
                                           rename(t.c_str(), f.c_str()); }
}
