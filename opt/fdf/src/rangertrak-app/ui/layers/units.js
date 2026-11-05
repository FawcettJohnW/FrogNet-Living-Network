// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/units.js -- [RT_LAYERS_V1] [RT_SUITE_APPS_V1] the utility and service units (John 2026-11-04: Team, Ambulance,
// Hospital, Shelter, Power, Water and others, separate because that is how people think of them): each connects as its
// unit (the Power crew, a shelter), sees its own kind, the hazards, the requests for help and services, and has the
// field's Actions and the Requests. A new unit is one line here (or its own layer, with its own screens).
'use strict'
RT.layer('units', (RT) => {
  const unit = (name, label, unitLabel, unitHint) => RT.role(name, { label, field: 'unit', unitLabel, unitHint, who: () => S.team,
    kinds: [name, 'hazard', 'help', 'service'], act: ['Actions', 'field'], req: ['Requests', 'requests'] })
  unit('water', 'Water', 'Water crew or site', 'Water crew 3')
  unit('power', 'Power', 'Power crew', 'Line crew 7')
  unit('shelter', 'Shelter', 'Shelter', 'Vashon High School gym')
  // [RT_TELECOM_NOT_PHONE_V1] the telephone company's crews: Telecom ("RangerTrak Phone" read as the app for a phone)
  unit('phone', 'Telecom', 'Telecom crew', 'Telecom crew 2')
})
