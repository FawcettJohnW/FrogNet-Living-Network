//  SuperTuxKart - state validator
//  Copyright (C) 2026 Fawcett Innovations LLC
//
//  This program is free software; you can redistribute it and/or
//  modify it under the terms of the GNU General Public License
//  as published by the Free Software Foundation; either version 3
//  of the License, or (at your option) any later version.
//  (Licensed to match SuperTuxKart, into whose tree this file goes.)

#ifndef HEADER_STATE_VALIDATOR_HPP
#define HEADER_STATE_VALIDATOR_HPP

#include <cstdio>
#include <string>

/** Records what the game believes, at the level ABOVE the network, on a fixed
 *  timer: every N world ticks, one line per kart this process knows about --
 *  tick, kart id, position, rotation, linear and angular velocity, speed,
 *  steer, energy, and whether it has finished.
 *
 *  It is the oracle for replacing the networking. It is compiled into BOTH
 *  builds unchanged. A stock run produces the reference; the replacement must
 *  drive every kart to the same places or it fails. It knows nothing about
 *  either network layer and reads only what World exposes.
 *
 *  Unlike ReplayRecorder it samples on a fixed interval keyed by world tick,
 *  not adaptively, so two logs line up row for row.
 *
 *  Enable with  --validator-log=FILE [--validator-every=TICKS]   (default 6).
 */
class StateValidator
{
private:
    static StateValidator *m_instance;
    FILE *m_file;
    int   m_every;
    int   m_last_tick;
    StateValidator(const std::string &path, int every);
public:
    ~StateValidator();
    static void create(const std::string &path, int every);
    static StateValidator *get() { return m_instance; }
    static void destroy();
    /** Call once per World::update, after the karts have been updated. */
    void update();
};

#endif
