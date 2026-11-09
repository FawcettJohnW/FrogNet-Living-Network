// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// eocadmin/eocadmin.js -- [UI_PREVIEW_V1] EOCAdmin: the EOC's paperwork, offline. The ICS forms (prefilled from the
// incident's records, kept as drafts, made final, printed), the IAP for an operational period, activity logs (ICS 214),
// the after-action report and improvement plan (HSEEP), and the offline package that carries it all to and from the RAM.
'use strict'
;(() => {
  const { $, $$, esc, when } = App
  let editing = null            // {id, form} the form open in the editor
  const forms = () => Store.read('adm-form').map(t => Object.assign({ id: t.instance, at: t.at }, t.bag))

  App.module('forms', 'Forms', (el) => {
    if (editing) return editor(el)
    const fs = forms()
    el.innerHTML = `<h2>Forms</h2><p class="lead">Each form opens filled in from what the EOC and the organizations have recorded. Change what you need, keep it as a draft, make it final, print it.</p>
      <div class="cards">${FORMS.all.map(f => `<div class="card click" data-new="${f.id}"><div class="t">${esc(f.name)}</div><div class="s">${esc(f.purpose)}</div></div>`).join('')}</div>
      <h3>Kept</h3>${App.table(['Form', 'Title', 'Op', 'State', 'Saved', ''], fs.map(x => [esc((FORMS.get(x.form) || {}).name || x.form), esc(x.title), esc(x.opPeriod), `<span class="pill${x.state === 'Final' ? ' on' : ''}">${esc(x.state)}</span>`, esc(when(x.at)),
        `<button type="button" class="btn sm" data-open="${x.id}">Open</button>`]), 'none yet')}`
    $$('[data-new]', el).forEach(c => c.onclick = () => { const f = FORMS.get(c.dataset.new); editing = { id: null, form: f.id, values: f.prefill() }; App.refresh() })
    $$('[data-open]', el).forEach(b => b.onclick = () => { const x = Store.get('adm-form', b.dataset.open); editing = { id: b.dataset.open, form: x.form, values: x.values, state: x.state }; App.refresh() })
  }, { icon: '📄', when: App.incident })

  function editor(el) {
    const f = FORMS.get(editing.form), fin = editing.state === 'Final'
    el.innerHTML = `<div class="row noprint"><button type="button" class="btn" id="back">← Forms</button>
        ${fin ? '' : '<button type="button" class="btn" id="refill" title="fill it again from the records">Fill again from the records</button>'}
        <button type="button" class="btn" id="draft"${fin ? ' hidden' : ''}>Keep as draft</button><button type="button" class="btn pri" id="final"${fin ? ' hidden' : ''}>Make final</button>
        <button type="button" class="btn" id="print">Print</button>${fin ? '<span class="pill on">Final: read only</span>' : ''}</div>
      <div class="paper"><h2>${esc(f.name)}</h2><div class="meta">${esc(f.purpose)}</div><div id="ff" style="margin-top:10px">${App.form(f.fields().map(x => Object.assign({ ro: fin }, x)), editing.values)}</div></div>`
    if (fin) $$('#ff textarea, #ff select', el).forEach(x => x.disabled = true)
    const save = (state) => {
      const values = App.collect($('#ff')), id = editing.id || Store.id('f')
      Store.write('adm-form', id, { form: f.id, title: values.subject || values.incident || f.name, values, state, opPeriod: ((App.incident() || {}).opPeriod || {}).n })
      editing = Object.assign(editing, { id, values, state }); App.status(f.name + (state === 'Final' ? ' made final' : ' kept as a draft'))
    }
    $('#ff').oninput = $('#ff').onchange = () => { if (!fin) editing.values = App.collect($('#ff')) }   // a redraw keeps what was typed
    $('#back').onclick = () => { editing = null; App.refresh() }
    if (!fin) {
      $('#refill').onclick = () => { editing.values = f.prefill(App.collect($('#ff'))); App.refresh() }
      $('#draft').onclick = () => save('Draft')
      $('#final').onclick = () => App.dialog('Make it final?', '<p>A final form is read only. Change it by starting a new one.</p>', [['Make final', () => { save('Final'); App.refresh() }, 'pri']])
      // a pick that names another record (204's organization, 213RR's request, 214's position) fills from that one
      $$('#ff select[name=to], #ff select[name=request]', el).forEach(s => s.onchange = () => { editing.values = f.prefill(App.collect($('#ff'))); App.refresh() })
    }
    $('#print').onclick = () => window.print()
  }

  // ------------------------------------------------------------------ the IAP
  App.module('iap', 'IAP', (el) => {
    const n = ((App.incident() || {}).opPeriod || {}).n
    const kept = forms().filter(x => Number(x.opPeriod) === Number(n))
    el.innerHTML = `<h2>Incident Action Plan · op period ${esc(n)}</h2>
      <p class="lead">The IAP is ICS ${FORMS.iap.join(', ')} for the period. Each one is the final you kept for it, else the newest draft, else filled from the records now.</p>
      <div class="row noprint"><button type="button" class="btn pri" id="pr">Print the IAP</button></div>
      ${FORMS.iap.map(id => { const f = FORMS.get(id), k = kept.filter(x => x.form === id).sort((a, b) => (b.state === 'Final') - (a.state === 'Final'))[0]
        const v = k ? k.values : f.prefill()
        return `<div class="paper" style="margin-bottom:14px"><h2>${esc(f.name)} <span class="pill${k && k.state === 'Final' ? ' on' : ''}">${k ? esc(k.state) : 'from the records'}</span></h2>
          ${App.table(['', ''], f.fields().filter(x => x.type !== 'heading').map(x => [esc(x.label), esc(v[x.k] == null ? '' : v[x.k]).replace(/\n/g, '<br>')]))}</div>` }).join('')}`
    $('#pr').onclick = () => window.print()
  }, { icon: '📘', when: App.incident })

  // ------------------------------------------------------------------ activity logs (ICS 214)
  App.module('logs', 'Activity logs', (el) => {
    const es = Store.read('adm-log').map(t => Object.assign({ id: t.instance }, t.bag))
    const pos = [...new Set(Model.staff().map(s => s.position).concat(es.map(e => e.position)))].filter(Boolean)
    let at; try { at = sessionStorage.getItem('openeoc-preview-logpos') } catch (e) {}
    at = at && pos.includes(at) ? at : pos[0] || ''
    el.innerHTML = `<h2>Activity logs</h2><p class="lead">ICS 214: each position's log. Entries are timed as they are written; the 214 form is built from them.</p>
      <div class="row"><select id="lp" style="max-width:320px">${pos.map(p => `<option${p === at ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select>
        <input id="lt" placeholder="notable activity" style="max-width:480px"><button type="button" class="btn pri" id="la">Log</button></div>
      ${App.table(['When', 'Position', 'Name', 'Activity'], es.filter(e => !at || e.position === at).map(e => [esc(when(e.at)), esc(e.position), esc(e.name), esc(e.text)]), 'no entries for this position')}`
    $('#lp').onchange = () => { try { sessionStorage.setItem('openeoc-preview-logpos', $('#lp').value) } catch (e) {} $('#lp').blur(); App.refresh() }
    $('#la').onclick = () => { const t = $('#lt').value.trim(), p = $('#lp').value; if (!t || !p) return
      Store.write('adm-log', Store.id('l'), { position: p, name: (Store.get('eoc-staff', p) || {}).name || '', text: t, at: new Date().toISOString() }) }
  }, { icon: '🖊', when: App.incident })

  // ------------------------------------------------------------------ after-action (HSEEP)
  App.module('aar', 'After-action', (el) => {
    const a = Store.get('adm-aar', 'current') || {}
    const ip = Store.read('adm-ip').map(t => Object.assign({ id: t.instance }, t.bag))
    el.innerHTML = `<h2>After-action report and improvement plan</h2><p class="lead">HSEEP: what went well, what to improve, and who does what by when.</p>
      <div id="af">${App.form([{ k: 'overview', label: 'Overview (dates, scope, participating organizations)', type: 'textarea', rows: 3 },
        { k: 'objectives', label: 'Objectives and core capabilities assessed', type: 'textarea', rows: 3 },
        { k: 'strengths', label: 'Strengths', type: 'textarea', rows: 4 }, { k: 'afi', label: 'Areas for improvement', type: 'textarea', rows: 4 },
        { k: 'conclusion', label: 'Conclusion', type: 'textarea', rows: 2 }], Object.assign({ overview: `${(App.incident() || {}).name}. Organizations: ${Model.orgs().map(o => o.name).join(', ')}.`,
          objectives: Model.objectives().map(o => `• ${o.text} [${o.state}]`).join('\n') }, a))}</div>
      <div class="row"><button type="button" class="btn pri" id="as">Save</button><button type="button" class="btn" id="ap">Print</button></div>
      <h3>Improvement plan</h3>
      <div id="ipf">${App.form([{ k: 'issue', label: 'Issue / area for improvement' }, { k: 'action', label: 'Corrective action' }, { k: 'capability', label: 'Core capability' },
        { k: 'owner', label: 'Responsible organization / POC' }, { k: 'start', label: 'Start', type: 'date' }, { k: 'done', label: 'Completion', type: 'date' }])}</div>
      <div class="row"><button type="button" class="btn" id="ipa">Add to the plan</button></div>
      ${App.table(['Issue', 'Corrective action', 'Capability', 'Owner', 'Start', 'Complete', ''], ip.map(x => [esc(x.issue), esc(x.action), esc(x.capability), esc(x.owner), esc(x.start), esc(x.done),
        `<button type="button" class="btn sm" data-x="${x.id}">Remove</button>`]), 'none yet')}`
    $('#as').onclick = () => { Store.write('adm-aar', 'current', App.collect($('#af'))); App.status('after-action report saved') }
    $('#ap').onclick = () => window.print()
    $('#ipa').onclick = () => { const v = App.collect($('#ipf')); if (!v.issue) return App.status('the issue', true); Store.write('adm-ip', Store.id('ip'), v) }
    $$('[data-x]', el).forEach(b => b.onclick = () => Store.remove('adm-ip', b.dataset.x))
  }, { icon: '🔁', when: App.incident })

  // ------------------------------------------------------------------ the offline package
  App.module('pkg', 'Offline package', (el) => {
    const notes = Store.read('preview-note').map(t => t.bag).filter(n => n.text)
    el.innerHTML = `<h2>Offline package</h2>
      <p class="lead">EOCAdmin works with no network: everything is on this device. The package carries it to another device, or into the RAM when the
        device reaches the network (in the preview: a file you save and open). Opening a package merges it, newest of each record wins.</p>
      <div class="row"><button type="button" class="btn pri" id="ex">Save the package</button><label class="btn" style="cursor:pointer">Open a package<input type="file" id="im" accept=".json" hidden></label>
        <button type="button" class="btn bad" id="rs">Erase everything here</button></div>
      <h3>Reviewer notes <span class="pill">${notes.length}</span></h3>
      ${App.table(['App', 'Screen', 'Note'], notes.map(n => [esc(n.app), esc(n.screen), esc(n.text).replace(/\n/g, '<br>')]), 'none: use ✎ Note on any screen')}`
    $('#ex').onclick = () => {
      const b = new Blob([Store.exportAll()], { type: 'application/json' }), a = document.createElement('a')
      a.href = URL.createObjectURL(b); a.download = 'openeoc-package-' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.json'; a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 2000)
    }
    $('#im').onchange = (e) => { const f = e.target.files[0]; if (!f) return
      f.text().then(t => { try { App.status(Store.importAll(t) + ' record(s) merged') } catch (x) { App.status('not an OpenEOC package: ' + x.message, true) } }) }
    $('#rs').onclick = () => App.dialog('Erase everything?', '<p>Every record in this preview, in every tab of this browser.</p>', [['Erase', () => { Store.reset(); location.reload() }, 'pri']])
  }, { icon: '💾' })

  App.module('none', 'No incident', (el) => { el.innerHTML = '<h2>No incident</h2><p class="lead">Start one in the EOC (or load its demo); EOCAdmin works from its records. Or open a package.</p>' },
    { icon: '🚨', when: () => !App.incident() })

  App.start({ app: 'eocadmin', title: 'OpenEOC · EOCAdmin', who: () => 'works offline · this device' })
})()
