//  SuperTuxKart - state validator
//  Copyright (C) 2026 Fawcett Innovations LLC
//
//  This program is free software; you can redistribute it and/or
//  modify it under the terms of the GNU General Public License
//  as published by the Free Software Foundation; either version 3
//  of the License, or (at your option) any later version.

#include "race/state_validator.hpp"

#include "karts/abstract_kart.hpp"
#include "karts/controller/kart_control.hpp"
#include "modes/world.hpp"
#include "utils/log.hpp"

StateValidator *StateValidator::m_instance = NULL;

// ----------------------------------------------------------------------------
StateValidator::StateValidator(const std::string &path, int every)
              : m_file(NULL), m_every(every < 1 ? 1 : every), m_last_tick(-1)
{
    m_file = fopen(path.c_str(), "w");
    if (!m_file)
    {
        // No fallback: a validator that cannot write is not a validator.
        Log::fatal("StateValidator", "Cannot open '%s' for writing.", path.c_str());
    }
    fprintf(m_file, "# stk-state-validator 1 every=%d\n", m_every);
    fprintf(m_file, "# tick kart x y z qx qy qz qw vx vy vz wx wy wz speed steer energy finished\n");
}   // StateValidator

// ----------------------------------------------------------------------------
StateValidator::~StateValidator()
{
    if (m_file) fclose(m_file);
}   // ~StateValidator

// ----------------------------------------------------------------------------
void StateValidator::create(const std::string &path, int every)
{
    destroy();
    m_instance = new StateValidator(path, every);
}   // create

// ----------------------------------------------------------------------------
void StateValidator::destroy()
{
    delete m_instance;
    m_instance = NULL;
}   // destroy

// ----------------------------------------------------------------------------
void StateValidator::update()
{
    World *world = World::getWorld();
    if (!world) return;
    const int tick = world->getTicksSinceStart();
    // World::update can run more than once for one tick during a rewind in the
    // stock build; a sample is taken the FIRST time a tick is reached and the
    // tick must be on the grid. Ticks only go forward in this log.
    if (tick <= m_last_tick || tick % m_every != 0) return;
    m_last_tick = tick;

    const unsigned int n = world->getNumKarts();
    for (unsigned int i = 0; i < n; i++)
    {
        const AbstractKart *k = world->getKart(i);
        if (k->isEliminated()) continue;
        const Vec3         &p = k->getXYZ();
        const btQuaternion  q = k->getRotation();
        const btVector3    &v = k->getBody()->getLinearVelocity();
        const btVector3    &w = k->getBody()->getAngularVelocity();
        fprintf(m_file,
                "%d %u %.4f %.4f %.4f %.5f %.5f %.5f %.5f "
                "%.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %d\n",
                tick, k->getWorldKartId(),
                p.getX(), p.getY(), p.getZ(),
                q.getX(), q.getY(), q.getZ(), q.getW(),
                v.getX(), v.getY(), v.getZ(),
                w.getX(), w.getY(), w.getZ(),
                k->getSpeed(), k->getControls().getSteer(), k->getEnergy(),
                k->hasFinishedRace() ? 1 : 0);
    }
    fflush(m_file);
}   // update
