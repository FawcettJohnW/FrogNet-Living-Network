// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/units.js -- [RT_LAYERS_V1] [RT_SUITE_APPS_V1] the utility and service units (John 2026-11-04: Team, Ambulance,
// Hospital, Shelter, Power, Water and others, separate because that is how people think of them): each connects as its
// unit (the Power crew, a shelter), sees its own kind, the hazards, the requests for help and services, and has the
// field's Actions and the Requests. A new unit is one line here (or its own layer, with its own screens).
'use strict'
RT.layer('units', (RT) => {
  const unit = (name, label, unitLabel) => RT.role(name, { label, field: 'unit', unitLabel, who: () => S.team,
    kinds: [name, 'hazard', 'help', 'service'], act: ['Actions', 'field'], req: ['Requests', 'requests'] })
  unit('water', 'Water', 'Water crew or site')
  unit('power', 'Power', 'Power crew')
  unit('shelter', 'Shelter', 'Shelter')
  unit('phone', 'Phone', 'Phone crew')
})
