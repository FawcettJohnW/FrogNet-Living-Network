// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// shared/fema.js -- [UI_PREVIEW_V1] the FEMA / NIMS reference the screens are built from: data, so a change of
// doctrine is a change here, not in a screen.
'use strict'
window.FEMA = {
  // NIMS EOC activation levels (NIMS 2017)
  levels: [
    { id: 3, name: 'Level 3 · Normal operations / steady state', short: 'L3 Steady state' },
    { id: 2, name: 'Level 2 · Enhanced steady state / partial activation', short: 'L2 Partial' },
    { id: 1, name: 'Level 1 · Full activation', short: 'L1 Full' },
  ],
  incidentTypes: ['Type 5', 'Type 4', 'Type 3', 'Type 2', 'Type 1'],
  // EOC organizational structures (NIMS): each one's sections and positions
  structures: {
    ics: { name: 'ICS or ICS-like', sections: {
      'Command': ['EOC Director', 'Public Information Officer', 'Safety Officer', 'Liaison Officer'],
      'Operations': ['Operations Section Chief', 'Branch Director', 'Division/Group Supervisor'],
      'Planning': ['Planning Section Chief', 'Situation Unit Leader', 'Resources Unit Leader', 'Documentation Unit Leader', 'Demobilization Unit Leader'],
      'Logistics': ['Logistics Section Chief', 'Supply Unit Leader', 'Communications Unit Leader', 'Facilities Unit Leader'],
      'Finance/Admin': ['Finance/Admin Section Chief', 'Time Unit Leader', 'Cost Unit Leader', 'Procurement Unit Leader'] } },
    ism: { name: 'Incident Support Model', sections: {
      'EOC Director': ['EOC Director', 'Public Information Officer', 'Liaison Officer'],
      'Situational Awareness': ['Situational Awareness Section Chief', 'GIS Specialist', 'Intelligence Analyst'],
      'Planning Support': ['Planning Support Section Chief', 'Current Planning', 'Future Planning', 'Technical Specialist'],
      'Resources Support': ['Resources Support Section Chief', 'Resource Ordering', 'Resource Tracking', 'Donations Management'],
      'Center Support': ['Center Support Section Chief', 'IT/Communications', 'Facilities', 'Finance'] } },
    dept: { name: 'Departmental', sections: {
      'Emergency Management': ['Emergency Manager', 'Duty Officer'],
      'Departments': ['Fire', 'Law Enforcement', 'Public Works', 'Public Health', 'Transportation', 'Utilities', 'Human Services'] } },
    esf: { name: 'Emergency Support Functions', sections: {
      'EOC Management': ['EOC Director', 'ESF Coordinator'],
      'ESFs': ['ESF #1 Transportation', 'ESF #2 Communications', 'ESF #3 Public Works and Engineering', 'ESF #4 Firefighting',
               'ESF #5 Information and Planning', 'ESF #6 Mass Care, Emergency Assistance, Temporary Housing and Human Services',
               'ESF #7 Logistics', 'ESF #8 Public Health and Medical Services', 'ESF #9 Search and Rescue',
               'ESF #10 Oil and Hazardous Materials Response', 'ESF #11 Agriculture and Natural Resources', 'ESF #12 Energy',
               'ESF #13 Public Safety and Security', 'ESF #14 Cross-Sector Business and Infrastructure', 'ESF #15 External Affairs'] } },
  },
  // the community lifelines (FEMA, 8 since 2023)
  lifelines: [
    { id: 'safety', name: 'Safety and Security', icon: '🛡' },
    { id: 'food', name: 'Food, Hydration, Shelter', icon: '🏠' },
    { id: 'health', name: 'Health and Medical', icon: '✚' },
    { id: 'energy', name: 'Energy (Power and Fuel)', icon: '⚡' },
    { id: 'comms', name: 'Communications', icon: '📡' },
    { id: 'transport', name: 'Transportation', icon: '🚚' },
    { id: 'hazmat', name: 'Hazardous Materials', icon: '☣' },
    { id: 'water', name: 'Water Systems', icon: '💧' },
  ],
  lifelineStatus: [
    { id: 'green', name: 'Stable' }, { id: 'yellow', name: 'Stabilizing' }, { id: 'red', name: 'Unstable' }, { id: 'grey', name: 'Unknown' },
  ],
  // the apps that join an EOC: each one is a category (John 2026-11-09: categories come from the apps that connect).
  // status: the fields its status report carries; lifeline: the lifeline it reports on; unit: what its members are
  apps: {
    team: { name: 'Team', plural: 'Teams', icon: '👥', lifeline: 'safety', unit: 'member',
      status: [{ k: 'state', label: 'State', type: 'select', options: ['Available', 'Assigned', 'Out of service'] },
               { k: 'people', label: 'People on the team', type: 'number' },
               { k: 'note', label: 'Note', type: 'text' }] },
    hospital: { name: 'Hospital', plural: 'Hospitals', icon: '🏥', lifeline: 'health', unit: 'staff',
      status: [{ k: 'state', label: 'Status', type: 'select', options: ['Open', 'Divert', 'Closed'] },
               { k: 'edBeds', label: 'ED beds available', type: 'number' },
               { k: 'icuBeds', label: 'ICU beds available', type: 'number' },
               { k: 'medsurg', label: 'Med/Surg beds available', type: 'number' },
               { k: 'power', label: 'Power', type: 'select', options: ['Grid', 'Generator', 'None'] },
               { k: 'note', label: 'Note', type: 'text' }] },
    ambulance: { name: 'Ambulance', plural: 'Ambulances', icon: '🚑', lifeline: 'health', unit: 'crew',
      status: [{ k: 'state', label: 'Status', type: 'select', options: ['Available', 'En route', 'On scene', 'Transporting', 'At hospital', 'Out of service'] },
               { k: 'level', label: 'Level', type: 'select', options: ['BLS', 'ALS'] },
               { k: 'dest', label: 'Destination', type: 'text' },
               { k: 'note', label: 'Note', type: 'text' }] },
    shelter: { name: 'Shelter', plural: 'Shelters', icon: '🏫', lifeline: 'food', unit: 'staff',
      status: [{ k: 'state', label: 'Status', type: 'select', options: ['Open', 'Full', 'Closed'] },
               { k: 'capacity', label: 'Capacity', type: 'number' },
               { k: 'occupancy', label: 'Occupancy', type: 'number' },
               { k: 'pets', label: 'Accepts pets', type: 'select', options: ['Yes', 'No'] },
               { k: 'note', label: 'Note', type: 'text' }] },
    fire: { name: 'Fire', plural: 'Fire', icon: '🚒', lifeline: 'safety', unit: 'crew',
      status: [{ k: 'state', label: 'Status', type: 'select', options: ['Available', 'Assigned', 'Out of service'] },
               { k: 'apparatus', label: 'Apparatus in service', type: 'number' }, { k: 'note', label: 'Note', type: 'text' }] },
    law: { name: 'Law Enforcement', plural: 'Law Enforcement', icon: '🚓', lifeline: 'safety', unit: 'officer',
      status: [{ k: 'state', label: 'Status', type: 'select', options: ['Normal', 'Stretched', 'Overwhelmed'] },
               { k: 'units', label: 'Units in service', type: 'number' }, { k: 'note', label: 'Note', type: 'text' }] },
    power: { name: 'Power Utility', plural: 'Power', icon: '⚡', lifeline: 'energy', unit: 'crew',
      status: [{ k: 'state', label: 'Grid', type: 'select', options: ['Normal', 'Partial outage', 'Major outage'] },
               { k: 'customersOut', label: 'Customers out', type: 'number' },
               { k: 'restore', label: 'Estimated restoration', type: 'text' }, { k: 'note', label: 'Note', type: 'text' }] },
    water: { name: 'Water Utility', plural: 'Water', icon: '💧', lifeline: 'water', unit: 'crew',
      status: [{ k: 'state', label: 'System', type: 'select', options: ['Normal', 'Boil-water notice', 'Do not use', 'Out'] },
               { k: 'pressure', label: 'Pressure', type: 'select', options: ['Normal', 'Low', 'None'] }, { k: 'note', label: 'Note', type: 'text' }] },
    publicworks: { name: 'Public Works', plural: 'Public Works', icon: '🚧', lifeline: 'transport', unit: 'crew',
      status: [{ k: 'state', label: 'Roads', type: 'select', options: ['Open', 'Some closures', 'Major closures'] },
               { k: 'closures', label: 'Closures', type: 'text' }, { k: 'note', label: 'Note', type: 'text' }] },
    comms: { name: 'Communications (ARES/RACES)', plural: 'Communications', icon: '📡', lifeline: 'comms', unit: 'operator',
      status: [{ k: 'state', label: 'Network', type: 'select', options: ['Normal', 'Degraded', 'Down'] },
               { k: 'nets', label: 'Nets up', type: 'text' }, { k: 'note', label: 'Note', type: 'text' }] },
  },
  // what a reported state means for its lifeline (anything not here: green)
  stateSev: { 'Out of service': 'yellow', 'Divert': 'yellow', 'Closed': 'red', 'Full': 'yellow', 'Stretched': 'yellow',
              'Overwhelmed': 'red', 'Partial outage': 'yellow', 'Major outage': 'red', 'Boil-water notice': 'yellow',
              'Do not use': 'red', 'Out': 'red', 'Some closures': 'yellow', 'Major closures': 'red', 'Degraded': 'yellow', 'Down': 'red' },
  // ICS 213RR / resource ordering: a request's states, in order
  requestStates: ['Requested', 'Approved', 'Ordered', 'En route', 'Arrived', 'Demobilized', 'Denied'],
  // a few NIMS-typed resources (RTLT names), for the request form's picker
  rtlt: ['Ambulance Strike Team (Ground)', 'Ambulance (Ground) Type 1 ALS', 'Ambulance (Ground) Type 2 BLS', 'Generator, 100 kW',
         'Potable Water Tender', 'Shelter Management Team', 'Mass Care Feeding Team', 'Search and Rescue Team, Type 3',
         'Engine, Fire (Type 1)', 'Debris Clearance Team', 'Public Information Officer', 'EOC Planning Section Chief', 'Other (describe)'],
  priorities: ['Urgent', 'High', 'Routine'],
}
