# RangerTrak's suite, mapped

RangerTrak `b6745f49`, 137 spec files, 1,344 `it` blocks (1,440 executed: some are table-driven). Each file is
applicable as it is, applicable with its setup changed (the service under it becomes a held view; its assertions stand),
or not applicable because the machinery it asserts is removed (`PLAN.md`, *What is thrown away*); those are mapped, not
deleted, with the reason. Decided here before phase 1 step 4; `QUESTION-3.md` takes any red that does not fit.


| | files | `it` blocks |
| --- | --- | --- |
| applicable as they are | 96 | 740 |
| applicable, setup changes | 14 | 183 |
| not applicable (machinery removed) | 27 | 421 |

| Spec | its | Mapping | Why |
| --- | --- | --- | --- |
| `after-action/ics214-export-dialog.component.spec.ts` | 4 | applicable |  |
| `app-shell/app-shell.component.spec.ts` | 1 | applicable |  |
| `app.component.spec.ts` | 3 | applicable |  |
| `domain/aar-note-migration.spec.ts` | 10 | not applicable | migrations: the shape becomes the cell bag's, once |
| `domain/activity-log.spec.ts` | 17 | applicable |  |
| `domain/attachments.spec.ts` | 23 | applicable |  |
| `domain/board-address.spec.ts` | 8 | not applicable | the Board |
| `domain/board-client.spec.ts` | 30 | not applicable | the Board |
| `domain/board-diagnosis.spec.ts` | 10 | not applicable | the Board |
| `domain/board-retry.spec.ts` | 5 | not applicable | the Board |
| `domain/clear-choices.spec.ts` | 8 | applicable |  |
| `domain/coordinate.spec.ts` | 14 | applicable |  |
| `domain/entry-merge.spec.ts` | 25 | not applicable | mergeEntries: one writer per entry, nothing to merge |
| `domain/grid-filter-chips.spec.ts` | 9 | applicable |  |
| `domain/import-diagnosis.spec.ts` | 18 | applicable |  |
| `domain/import-safety.spec.ts` | 14 | not applicable | import safety copy |
| `domain/layers.spec.ts` | 13 | applicable |  |
| `domain/location-merge.spec.ts` | 9 | applicable |  |
| `domain/map-entry-filter.spec.ts` | 14 | applicable |  |
| `domain/map-print-layout.spec.ts` | 26 | applicable |  |
| `domain/mission-journal.spec.ts` | 11 | applicable |  |
| `domain/mission-migration.spec.ts` | 47 | not applicable | migrations |
| `domain/mission-summary.spec.ts` | 33 | applicable |  |
| `domain/msg-no.spec.ts` | 21 | applicable |  |
| `domain/op-period.spec.ts` | 5 | applicable |  |
| `domain/overdue.spec.ts` | 12 | applicable |  |
| `domain/packet-time-check.spec.ts` | 9 | not applicable | Report Packet: no packet to time-check |
| `domain/places-file.spec.ts` | 27 | applicable |  |
| `domain/places-print.spec.ts` | 7 | applicable |  |
| `domain/radio-log-migration.spec.ts` | 19 | not applicable | migrations |
| `domain/ranger-match.spec.ts` | 12 | applicable |  |
| `domain/ranger-migration.spec.ts` | 42 | not applicable | migrations |
| `domain/ranger-photo-export.spec.ts` | 7 | applicable |  |
| `domain/report-packet-files.spec.ts` | 9 | not applicable | Report Packet: a field device writes its own entry cells |
| `domain/restore-preview.spec.ts` | 9 | not applicable | restore |
| `domain/roster-changes.spec.ts` | 8 | applicable |  |
| `domain/roster-print.spec.ts` | 5 | applicable |  |
| `domain/roster-sheet.spec.ts` | 27 | applicable |  |
| `domain/route-guess.spec.ts` | 4 | applicable |  |
| `domain/scenario.spec.ts` | 13 | applicable |  |
| `domain/station-label.spec.ts` | 5 | applicable |  |
| `domain/usage-state.spec.ts` | 2 | applicable |  |
| `domain/view.spec.ts` | 18 | applicable |  |
| `entry/entry-draft.service.spec.ts` | 4 | applicable |  |
| `entry/entry.component.spec.ts` | 7 | applicable |  |
| `entry/location.component.spec.ts` | 7 | applicable |  |
| `entry/mini-mapLeaflet.component.spec.ts` | 2 | applicable |  |
| `lazy/help/help.component.spec.ts` | 4 | applicable |  |
| `lazy/help/tabs/help-about.component.spec.ts` | 2 | applicable |  |
| `lazy/help/tabs/help-feedback.component.spec.ts` | 3 | applicable |  |
| `lazy/shared/services/three-words/three-words.service.spec.ts` | 1 | applicable |  |
| `log/log.component.spec.ts` | 3 | applicable |  |
| `map/map-engine.service.spec.ts` | 3 | applicable |  |
| `map/map-page/map-page.component.spec.ts` | 4 | applicable |  |
| `map/mapLibre.component.spec.ts` | 3 | applicable |  |
| `mapLeaflet/leaflet-offline-controller.spec.ts` | 2 | applicable |  |
| `mapLeaflet/mapLeaflet.component.spec.ts` | 3 | applicable |  |
| `mapLeaflet/offline-tile-save.spec.ts` | 11 | applicable |  |
| `mission/mission.component.spec.ts` | 9 | applicable, setup changes | its service fake; backup/restore controls go |
| `mission/sections/mission-field-report-statuses/mission-field-report-statuses.component.spec.ts` | 5 | applicable |  |
| `mission/sections/restore-preview-dialog/restore-preview-dialog.component.spec.ts` | 3 | not applicable | restore |
| `radio-log/radio-log.component.spec.ts` | 3 | applicable, setup changes | its service fake |
| `rangers/rangers.component.spec.ts` | 4 | applicable, setup changes | its service fake |
| `shared/aar-note/aar-note-dialog.component.spec.ts` | 15 | applicable |  |
| `shared/ai-photo.spec.ts` | 4 | applicable |  |
| `shared/alerts/alerts.component.spec.ts` | 1 | applicable |  |
| `shared/attachments/attachment-hover-preview.spec.ts` | 7 | applicable |  |
| `shared/attachments/attachment-intake.service.spec.ts` | 4 | applicable |  |
| `shared/attachments/compress/compress.spec.ts` | 17 | applicable |  |
| `shared/attachments/compress/exif.spec.ts` | 5 | applicable |  |
| `shared/attachments/compress/media-tags.spec.ts` | 4 | applicable |  |
| `shared/back-to-top/back-to-top.component.spec.ts` | 3 | applicable |  |
| `shared/clear-dialog/clear-dialog.component.spec.ts` | 5 | applicable |  |
| `shared/crypto/encrypted-file.spec.ts` | 11 | applicable |  |
| `shared/escape-html.spec.ts` | 3 | applicable |  |
| `shared/export/handover-file.spec.ts` | 19 | not applicable | hand-over file: the next scribe holds a read on the same families |
| `shared/export/ics213-pdf.spec.ts` | 9 | applicable |  |
| `shared/export/ics214-pdf.spec.ts` | 11 | applicable |  |
| `shared/export/ics309-log.spec.ts` | 18 | applicable |  |
| `shared/export/import-file.spec.ts` | 2 | applicable |  |
| `shared/export/local-date.spec.ts` | 2 | applicable |  |
| `shared/export/mission-summary-csv.spec.ts` | 4 | applicable |  |
| `shared/export/mission-zip.spec.ts` | 17 | not applicable | mission backup zip |
| `shared/export/print-title.spec.ts` | 4 | applicable |  |
| `shared/export/report-packet.spec.ts` | 15 | not applicable | Report Packet |
| `shared/feedback/feedback.component.spec.ts` | 6 | applicable |  |
| `shared/filter-chips/filter-chips.component.spec.ts` | 4 | applicable |  |
| `shared/footer/footer.component.spec.ts` | 1 | applicable |  |
| `shared/grid-width-cap.spec.ts` | 3 | applicable |  |
| `shared/guide/guide.service.spec.ts` | 2 | applicable |  |
| `shared/guide/help-search.spec.ts` | 7 | applicable |  |
| `shared/header/header.component.spec.ts` | 10 | applicable |  |
| `shared/import-safety/import-safety-card.component.spec.ts` | 8 | not applicable | import safety copy |
| `shared/install-update/install-update.component.spec.ts` | 8 | not applicable | service worker update prompt |
| `shared/mapping/cache-first-source.spec.ts` | 4 | applicable |  |
| `shared/mapping/demo-map.spec.ts` | 12 | applicable |  |
| `shared/mapping/extent.spec.ts` | 4 | applicable |  |
| `shared/mapping/google-geocoder.spec.ts` | 2 | applicable |  |
| `shared/mapping/location-marker.spec.ts` | 11 | applicable |  |
| `shared/mission-readiness/mission-readiness.component.spec.ts` | 6 | applicable |  |
| `shared/navbar/navbar.component.spec.ts` | 4 | applicable |  |
| `shared/ranger-image.spec.ts` | 2 | applicable |  |
| `shared/redact.spec.ts` | 4 | applicable |  |
| `shared/services/aar-note.service.spec.ts` | 8 | applicable, setup changes | held view on (notes, <mission>, *) |
| `shared/services/activity-log-header.service.spec.ts` | 4 | applicable |  |
| `shared/services/attachment-store.service.spec.ts` | 11 | applicable, setup changes | attachments: per device today; QUESTION-3 |
| `shared/services/backup-attachments.spec.ts` | 8 | not applicable | mission backup (attachments zip) |
| `shared/services/backup.service.spec.ts` | 38 | not applicable | mission backup/restore: the mission is in the post's RAM |
| `shared/services/device-prefs.service.spec.ts` | 6 | applicable |  |
| `shared/services/diagnostics.service.spec.ts` | 5 | applicable |  |
| `shared/services/field-mode.service.spec.ts` | 2 | applicable, setup changes | fieldModeSentUids goes (a write that landed is sent); the rest stands |
| `shared/services/global-error-handler.spec.ts` | 3 | applicable |  |
| `shared/services/import-safety-hold.spec.ts` | 5 | not applicable | import safety copy |
| `shared/services/import-safety.service.spec.ts` | 15 | not applicable | import safety copy |
| `shared/services/installable.service.spec.ts` | 1 | applicable |  |
| `shared/services/mission-journal.service.spec.ts` | 7 | applicable |  |
| `shared/services/mission-readiness.service.spec.ts` | 11 | applicable |  |
| `shared/services/mission.service.spec.ts` | 9 | applicable, setup changes | held view on (mission, settings, *) |
| `shared/services/print-copies.service.spec.ts` | 2 | applicable |  |
| `shared/services/radio-log.service.spec.ts` | 41 | applicable, setup changes | the log becomes a held view on (log, <mission>.<op>, *) |
| `shared/services/ranger-photo.service.spec.ts` | 14 | applicable, setup changes | (photos, <mission>, <uid>), read on demand |
| `shared/services/ranger.service.spec.ts` | 37 | applicable, setup changes | held view on (roster, <mission>, *) |
| `shared/services/report-packet-files.spec.ts` | 5 | not applicable | Report Packet |
| `shared/services/sample-data-montecito.spec.ts` | 8 | applicable, setup changes | demo scenarios load into cells |
| `shared/services/sample-data-oso.spec.ts` | 10 | applicable, setup changes | demo scenarios load into cells |
| `shared/services/sample-data.service.spec.ts` | 12 | applicable, setup changes | demo scenarios load into cells |
| `shared/services/storage-persistence.service.spec.ts` | 4 | not applicable | storage persistence request (IndexedDB eviction) |
| `shared/services/usage-state.service.spec.ts` | 3 | applicable |  |
| `shared/services/view.service.spec.ts` | 14 | applicable |  |
| `shared/stale-origin-notice/stale-origin-notice.component.spec.ts` | 4 | applicable |  |
| `shared/storage/record-encryption.spec.ts` | 15 | applicable, setup changes | records at rest: QUESTION-3 (per-device secrets stay) |
| `shared/storage/record-store.spec.ts` | 34 | not applicable | RecordStore: a Ribbit space per device |
| `shared/storage/tab-lock.spec.ts` | 5 | not applicable | tab lock: no tab holds the state |
| `shared/time-picker/time-picker.component.spec.ts` | 9 | applicable |  |
| `shared/utility.spec.ts` | 14 | applicable |  |
| `shared/view/more-about.component.spec.ts` | 5 | applicable |  |
| `x404/x404.component.spec.ts` | 1 | applicable |  |
