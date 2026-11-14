// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// layers/public.js -- [RT_LAYERS_V1] [RT_PUBLIC_HOME_V1] the public's view (John, 2026-11-01: "find a friend, log a
// request for help, file a report with the EOC ... plus the big map"): it opens on Home, what a person can do -- I need
// help, I'm safe, Find someone, Report to the EOC, Hospitals & services, the survival library, FEMA -- and a map of
// hospitals, fires, outages, help and hazards only. No chat. Needs the help layer (its requests, where(), actionsFor()).
'use strict'
RT.layer('public', (RT) => {
  RT.role('public', { label: 'Public', field: 'none', who: () => 'Public', kinds: ['hospital', 'fire', 'power', 'water', 'shelter', 'help', 'hazard'],
                      only: ['hospital', 'fire', 'power', 'help', 'hazard'], chat: false, sensors: false, mission: false, home: true,
                      legend: '<span><i style="background:#fff"></i>Hospital</span><span><i style="background:#ff6d00"></i>Fire</span><span><i style="background:#7e57c2"></i>Power outage</span>',
                      connected() { $('#homeBtn').hidden = false; $('#home').hidden = false } })
  const where = RT.where, actionsFor = RT.actionsFor
  $('#sheet').insertAdjacentHTML('beforebegin', `<section id="home" hidden>
    <div class="hero">
      <div class="hero-t"><div class="hero-k">OpenEOC</div><div class="hero-m" id="heroMission">connected</div></div>
      <div class="hero-s" id="heroSummary"></div>
    </div>
    <div class="callout">In danger right now? <b>Call 911</b> if you can. If you can't, ask for help here: responders see it at once.</div>
    <div class="primary">
      <button class="act red" data-go="needhelp"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z"/></svg><b>I need help</b><span>medical · trapped · water · shelter</span></button>
      <button class="act green" data-go="safe"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg><b>I'm safe</b><span>tell family and responders</span></button>
    </div>
    <div class="cards">
      <button class="card2" data-go="find"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"/></svg><b>Find someone</b><span>who has checked in, by name</span></button>
      <button class="card2" data-go="report"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg><b>Report to the Command Center</b><span>damage · hazards · what you see</span></button>
      <button class="card2" data-go="places"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg><b>Hospitals &amp; services</b><span>beds · fires · power outages</span></button>
      <button class="card2" data-go="library"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM6 4h5v8l-2.5-1.5L6 12V4z"/></svg><b>Survival library</b><span>first aid · water · shelter · safety</span></button>
      <button class="card2" data-go="fema"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1 3 5v6c0 5.6 3.8 10.7 9 12 5.2-1.3 9-6.4 9-12V5l-9-4z"/></svg><b>Contact FEMA</b><span>apply for disaster assistance</span></button>
      <button class="card2 mapcard" data-go="map"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 3l-.2.1L15 5.1 9 3 3.4 4.9a.5.5 0 0 0-.4.5V20.5a.5.5 0 0 0 .5.5l.2-.1L9 18.9l6 2.1 5.6-1.9a.5.5 0 0 0 .4-.5V3.5a.5.5 0 0 0-.5-.5zM15 19l-6-2.1V5l6 2.1V19z"/></svg><b>Map</b><span>hospitals, hazards and help near you</span></button>
    </div>
  </section>`)
  RT.button('homeBtn', 'Home', () => { $('#home').hidden = false; $('#sheet').hidden = true }, '', '#misBtn')
  document.querySelectorAll('#home [data-go]').forEach(t => t.onclick = () => go(t.dataset.go))
  RT.hook('connected', () => { if (!RT.me().home) $('#home').hidden = true })
  RT.hook('showOnMap', () => { $('#home').hidden = true })
  RT.hook('mission', (m) => { $('#heroMission').textContent = m.name || m.id })
  RT.hook('world', () => heroSummary())
  RT.on('help', () => heroSummary()); RT.on('facilities', () => heroSummary())
  RT.hook('back', () => { if (RT.me().home && $('#home').hidden && !$('#main').hidden) { $('#home').hidden = false; return true } return false })
  RT.hook('refresh', (what) => {
    if (S.sheet === 'help' && what === 'help') myRequests()
    else if (S.sheet === 'find' && what === 'help') findDraw(document.querySelector('#sheet .q').value)
  })
const NEEDS = ['medical', 'trapped', 'evacuation', 'water', 'food', 'shelter', 'medication', 'power', 'other']   // DisasterComm's help page's
const mine = (() => { try { return JSON.parse(localStorage.getItem('rt.mine') || '{}') } catch (e) { return {} } })()
const keep = () => { try { localStorage.setItem('rt.mine', JSON.stringify(mine)) } catch (e) { } }
const rid0 = () => 'r-' + S.cfg.dev + '-' + Math.random().toString(36).slice(2, 7)
function go(what) {
  if (what === 'map') { $('#home').hidden = true; return }
  if (what === 'needhelp' || what === 'safe') return helpForm(what === 'safe' ? 'safe' : 'open')
  if (what === 'find') return findForm()
  if (what === 'report') return reportForm()
  if (what === 'places') return placesList()
  if (what === 'library') return library()
  if (what === 'fema') return fema()
}
function whereHtml() {
  return `<label>Where (address, landmark)<input name="place"></label>
    <div class="row2"><button type="button" class="loc">Use my location</button> <span class="locsay"></span></div>`
}
function wireWhere(f) {
  const say = f.querySelector('.locsay'), b = f.querySelector('.loc'); let got = myFix
  if (got) say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4)
  b.onclick = () => {
    if (myFix) { got = myFix; say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4); return }
    if (!navigator.geolocation) { say.textContent = 'this device gives no location: say where above'; return }
    navigator.geolocation.getCurrentPosition(p => { got = { lat: p.coords.latitude, lon: p.coords.longitude }; say.textContent = 'your location: ' + got.lat.toFixed(4) + ', ' + got.lon.toFixed(4) },
      e => { say.textContent = 'no location (' + e.message + '): say where above' })
  }
  return () => { const l = { text: f.place.value.trim() }; if (got) { l.lat = got.lat; l.lon = got.lon } return l }
}
function helpForm(status) {
  const safe = status === 'safe'
  sheet('form', safe ? "I'm safe" : 'I need help', `<form class="hf">
    <label>Name (or who this is for)<input name="name" required value="${esc(S.cfg.name || '')}"></label>
    <label>How many people<input name="people" type="number" min="1" value="1"></label>
    ${safe ? '' : `<div>What is needed</div><div class="needs">${NEEDS.map(n => `<label><input type="checkbox" value="${n}"> ${n}</label>`).join('')}</div>`}
    ${whereHtml()}
    <label>How to reach you (phone, radio)<input name="contact"></label>
    <label>Anything else<textarea name="note" rows="3"></textarea></label>
    <button class="go">${safe ? 'Tell everyone I am safe' : 'Ask for help'}</button></form>
    <div class="meta">${safe ? 'Your family can find you by name under Find someone.' : 'Responders and the Command Center see this at once, on their maps.'}</div>`)
  const f = document.querySelector('.hf'), loc = wireWhere(f)
  f.onsubmit = (e) => {
    e.preventDefault()
    const rid = rid0()
    const bag = { name: f.name.value.trim(), people: +f.people.value || 1, needs: [...f.querySelectorAll('.needs input:checked')].map(c => c.value),
                  status, location: loc(), contact: f.contact.value.trim(), note: f.note.value.trim() }
    send({ op: 'help', rid, bag }); mine[rid] = bag; keep()
    myRequests()
  }
}
function myRequests() {
  const rows = Object.keys(mine).map(rid => {
    const b = S.help[rid] || mine[rid], acts = actionsFor(rid)
    return `<div class="card"><b>${esc(b.name)}</b> <span class="s-${esc(b.status)}">${b.status === 'open' ? 'needs help' : esc(b.status)}</span>
      <div class="meta">${S.help[rid] ? 'published' : 'sending…'} · ${esc(where(b.location))}</div>
      ${acts.map(a => `<div class="meta">↳ ${esc(a.action)} by ${esc(a.unit)}${a.note ? ' — ' + esc(a.note) : ''}</div>`).join('')}
      ${b.status === 'open' ? `<button type="button" onclick="resolveMine('${esc(rid)}')">I don't need help any more</button>` : ''}</div>`
  }).join('')
  sheet('help', 'Your requests', rows || '<div class="meta">None yet.</div>')
}
window.resolveMine = (rid) => { const b = Object.assign({}, S.help[rid] || mine[rid], { status: 'resolved' }); mine[rid] = b; keep(); send({ op: 'help', rid, bag: b }) }
function findForm() {
  sheet('find', 'Find someone', `<label>Name<input class="q" placeholder="type a name"></label><div class="found"></div>`)
  const q = document.querySelector('#sheet .q'); q.oninput = () => findDraw(q.value); q.focus(); findDraw('')
}
function findDraw(text) {
  const t = text.trim().toLowerCase(), box = document.querySelector('#sheet .found'); if (!box) return
  if (t.length < 2) { box.innerHTML = '<div class="meta">Type at least two letters of the name. Everyone who said they are safe, or asked for help, is here.</div>'; return }
  const hits = Object.entries(S.help).filter(([, b]) => (b.name || '').toLowerCase().includes(t)).sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
  box.innerHTML = hits.length ? hits.map(([rid, b]) => `<div class="card"><b>${esc(b.name)}</b> <span class="s-${esc(b.status)}">${b.status === 'open' ? 'asked for help' : b.status === 'safe' ? 'is safe' : 'help no longer needed'}</span>
      <div class="meta">${esc(where(b.location))}${b.contact ? ' · ' + esc(b.contact) : ''} · ${b.at ? new Date(b.at * 1000).toLocaleString() : ''}</div>
      ${b.note ? `<div>${esc(b.note)}</div>` : ''}</div>`).join('') : '<div class="meta">No one by that name has checked in yet.</div>'
}
function reportForm() {
  sheet('form', 'Report to the Command Center', `<form class="rf">
    <label>What is it<select name="category"><option>Damage</option><option>Hazard</option><option>Fire</option><option>Flooding</option><option>Power line down</option><option>Road blocked</option><option>Missing person</option><option>Other</option></select></label>
    <label>What do you see<textarea name="text" rows="4" required></textarea></label>
    ${whereHtml()}<button class="go">Send to the Command Center</button></form>`)
  const f = document.querySelector('.rf'), loc = wireWhere(f)
  f.onsubmit = (e) => {
    e.preventDefault(); const l = loc()
    send({ op: 'report', category: f.category.value, text: f.text.value.trim() + (l.text ? ' (at ' + l.text + ')' : ''), lat: l.lat, lng: l.lon })
    sheet('form', 'Report to the Command Center', '<div class="card">Sent. The Command Center has it now.</div>')
  }
}
function placesList() {
  const by = (k) => Object.values(S.pos).filter(b => b && b.kind === k)
  const h = by('hospital').sort((a, b) => (b.beds || 0) - (a.beds || 0)).map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${b.beds ?? '?'} beds available</div></div>`).join('')
  const f = by('fire').map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${esc(b.severity || '')} — keep clear</div></div>`).join('')
  const p = by('power').map(b => `<div class="card"><b>${esc(b.name)}</b><div class="meta">${b.customers_out || '?'} customers without power</div></div>`).join('')
  sheet('places', 'Hospitals & services', `<h3>Hospitals</h3>${h || '<div class="meta">none known</div>'}<h3>Fires</h3>${f || '<div class="meta">none</div>'}<h3>Power outages</h3>${p || '<div class="meta">none</div>'}`)
}

function heroSummary() {
  if (!$('#heroSummary')) return
  const v = Object.values(S.pos), haz = Object.values(S.field || {}).filter(b => b.what === 'hazard').length
  const beds = v.filter(b => b && b.kind === 'hospital' && (b.beds || 0) > 0).length
  const safe = Object.values(S.help || {}).filter(b => b.status === 'safe').length
  $('#heroSummary').innerHTML = `${beds} hospitals with beds · ${haz} hazards marked<br>${safe} people checked in safe`
}

// ------------------------------------------------------------ the survival library: in the app, so it works with no network
const LIBRARY = [
  ['Emergencies: call first', ['Call 911 for life-threatening emergencies if any phone works. Text 911 where it is supported.',
    'If you cannot call, use "I need help" here: responders and the Command Center see it at once, with where you are.',
    'Three of anything (whistle blasts, shouts, flashes, rock piles) is a call for help.']],
  ['Bleeding and first aid', ['Press firmly on the wound with a clean cloth and keep pressing. Do not lift it to look.',
    'For bleeding from an arm or leg that pressure will not stop: a tourniquet 2-3 inches above the wound, tight until the bleeding stops; note the time.',
    'Shock (pale, cold, clammy, confused): lay the person down, keep them warm, nothing to eat or drink, get help.',
    'Do not move someone who may have a neck or back injury unless they are in danger where they are.']],
  ['Drinking water', ['Plan on one gallon per person per day, for drinking and hygiene.',
    'Boil clear water for 1 minute (3 minutes above 6,500 feet), then let it cool.',
    'If you cannot boil: 8 drops (1/8 teaspoon) of plain unscented household bleach (6%) per gallon of clear water; stir and wait 30 minutes. Double it for cloudy water.',
    'Water from the water heater and ice cubes is usually safe. Water from flood areas is not.']],
  ['Carbon monoxide', ['Never run a generator, grill, camp stove or charcoal indoors, in a garage, or near a window or door.',
    'Keep a generator at least 20 feet from the house, exhaust pointed away.',
    'Headache, dizziness or nausea in several people at once: get everyone into fresh air now and call for help.']],
  ['Earthquake', ['During: Drop, Cover, Hold On. Stay away from windows. Do not run outside.',
    'After: expect aftershocks. If you smell gas, leave, and do not use flames, switches or phones inside.',
    'Check for injuries, then hazards: gas, water, power lines, broken glass. Wear shoes.']],
  ['Flooding', ["Turn around, don't drown: six inches of moving water can knock you down; a foot can float a car.",
    'Stay off bridges over fast water. Do not touch electrical equipment if you are wet or in water.',
    'Move to higher ground. Flood water carries sewage and chemicals: wash anything it touches.']],
  ['Power outage and food', ['Keep the fridge and freezer closed: food stays cold about 4 hours in a closed fridge, 48 hours in a full freezer (24 half full).',
    'Throw away perishable food that has been above 40 °F for 2 hours or more. When in doubt, throw it out.',
    'Unplug appliances to protect them from the surge when power comes back.']],
  ['Fire and smoke', ['Get out, stay out. Crawl low under smoke. Feel doors before opening; a hot door means use another way.',
    'If your clothes catch fire: stop, drop, and roll.',
    'For wildfire smoke, stay indoors with windows closed; an N95 mask helps outdoors.']],
  ['Shelter and warmth', ['Stay dry: wet clothing loses most of its warmth. Layer, cover your head, insulate yourself from the ground.',
    'Shivering, confusion, slurred speech: hypothermia. Get them warm and dry, warm drinks if they are awake, and get help.',
    'Shelters are under Hospitals & services as the Command Center opens them.']],
]
function library() {
  sheet('library', 'Survival library', `<div class="lib"><div class="meta">Kept in this app: it works with no network at all.</div>` +
    LIBRARY.map(([t, items], i) => `<details${i === 0 ? ' open' : ''}><summary>${esc(t)}</summary><ul>${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`).join('') +
    `<div class="src">From standard public guidance (Ready.gov, the American Red Cross, CDC). In an emergency, follow what responders tell you.</div></div>`)
}
function fema() {
  sheet('fema', 'Contact FEMA', `<div class="lib">
    <div class="card"><b>Apply for disaster assistance</b><ul>
      <li>Online: <b>DisasterAssistance.gov</b></li>
      <li>By phone: <b>1-800-621-3362</b></li>
      <li>On a phone: the free <b>FEMA app</b>, from your app store</li>
      <li>In person: a <b>Disaster Recovery Center</b> (DisasterAssistance.gov lists them once they open)</li></ul></div>
    <div class="card"><b>Have ready</b><ul><li>Your address at the time of the disaster, and where you can be reached now</li>
      <li>Insurance information</li><li>Bank account details, for direct deposit</li></ul></div>
    <div class="card"><b>No Internet here?</b> Use "Report to the Command Center": the Command Center can pass a FEMA request on for you.</div>
    <div class="src">From USA.gov, "How to apply for disaster assistance" (checked 2026-11-01).</div></div>`)
}
})
