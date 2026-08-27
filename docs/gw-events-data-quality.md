# gw_events data quality: deletions and known residue

This note records a one-time production data cleanup and a related gap found while investigating it, so neither needs to be reconstructed from old chat history. Verified 2026-08.

## Deleted: blind_injection, GRB051103

Two rows were deleted from `gw_events` in production on 2026-08 (see git history for this file's introduction for the exact date):

- `blind_injection` — a hardware calibration test (a blind injection exercise), not a real gravitational wave detection.
- `GRB051103` — a GRB-counterpart search trigger with no confirmed GW detection.

Both were confirmed, via the live GWOSC API (`https://gwosc.org/eventapi/json/allevents/`), to carry `catalog.shortName == "Initial_LIGO_Virgo"` — GWOSC's own placeholder catalog for entries with no astrophysical detection, not a real event catalog. They were never real GW events, so they were deleted outright rather than kept as documented residue.

Before deleting, `gw_candidates` (the only table with a foreign key onto `gw_events.superevent_id`) was confirmed to hold zero rows for either `superevent_id` — in fact zero rows at all in production at the time — and no other table or column in the schema references `superevent_id` in any form. `gw_events` totaled 446 rows before the deletion, 444 after; neither `superevent_id` resolves any longer.

## Kept: 43 unclassified-but-real rows (IAS-O3a, GWTC-2.1-auxiliary)

Separately, 43 rows classified as significance-tier "unknown" were deliberately left in place, not deleted. Unlike the two rows above, these are real GW candidate entries from real catalogs (IAS-O3a, a third-party reanalysis catalog, and GWTC-2.1-auxiliary, an LVK auxiliary release) that this codebase's significance classifier doesn't yet have a confident, marginal, or preliminary tier for. They're documented residue — real data outside current classification coverage — not junk, and should not be deleted in a future pass without separately re-confirming that decision.

## Known gap: properties.catalog isn't backfilled on refresh

While investigating the two rows above, their `properties` JSONB was found to have no `catalog` key at all, which looked at first like the wrong rows had been identified. It wasn't: `properties.catalog` was only added to the ingestion code in commit `63f8875` ("Add GW significance classification and /api/gw/stats", 2026-07-23), two months after these two rows were originally seeded (2026-05-19). `seed_gw_events`'s weekly refresh merges new non-None values into `properties` for every row GWOSC's live feed still returns, and GWOSC's feed does still return both of these, so in principle a refresh since 2026-07-23 should have backfilled `catalog` onto them. It apparently hasn't.

This is flagged as worth a look, not fixed here: it's out of scope for a one-time row deletion, and the two rows in question are gone anyway. But if other pre-2026-07-23 rows are silently missing `properties.catalog` too, anything relying on that field for classification or filtering may be under-counting for rows seeded before the field existed.

## Retired superevent IDs: the reconciliation mechanism

Verified 2026-08-27 against the live GWOSC feed (433 distinct `commonName`s, 671 version-keyed entries).

`seed_gw_events` upserts on exact `superevent_id` match, so it can only ever touch IDs GWOSC still publishes. An ID GWOSC has *stopped* publishing is invisible to it and would sit in `gw_events` forever, potentially alongside the successor row for the same physical event. `reconcile_retired_events` is the other half of that pass: it soft-flags such rows with `retired_at` and never deletes them, because a retired row may carry locally computed `gw_candidates` or skymap data and is the only surviving record that the ID was ever served.

### Orphan detection compares against the whole feed, not the ingestable subset

An orphan is a row whose `superevent_id` is absent from GWOSC **entirely**. It is deliberately not "absent from `fetch_gwosc_events()`": that function drops entries by catalog tag (IAS-O3a, `Initial_LIGO_Virgo`, GWTC-2.1-auxiliary — see `_EXCLUDED_CATALOGS`), and the rows behind those tags are still served by GWOSC. Diffing against the ingestable list would mis-flag every one of them as retired. `fetch_gwosc_catalog_index()` therefore returns the full `commonName` set, excluded catalogs included.

### Successors come only from GWOSC's own rename record

GWOSC keys each feed entry `"{name}-v{N}"` while `commonName` holds the event's *current* name, so a key whose name differs from its `commonName` is a rename GWOSC itself documents. That is the only authoritative rename evidence the public API exposes — there is no per-event changelog endpoint (`/eventapi/json/event/{name}/` 404s for a retired name and returns only the latest version for a live one).

Reconciliation acts on that and nothing else. In particular it does **not** infer a successor from `event_time` proximity: across all 433 events in the live feed, GWOSC's GPS time never moves by more than **0.1 s** between versions of the same event (checked over all 190 multi-version events). GWOSC does not issue multi-hour trigger-time corrections between catalog releases, so an hours-wide gap between a stored `event_time` and a same-day candidate argues *against* a rename, not for one. An orphan with no documented successor keeps `superseded_by` NULL and is reported separately — the "a human must decide" state.

### Current state: zero orphans, and why that is correct

**There are currently no orphans in production.** A dry run against the live table finds nothing to reconcile, and that is the correct result, not a broken detector.

The 13 orphans this mechanism was originally written against were subsequently found to be fabricated rows — never produced by any committed code — and were deleted outright in 2026-08 along with the fabricated keys stripped from five further rows. That cleanup is documented below. Two of the 13, `GW200105` and `GW200115`, were initially believed to be genuine renames resolvable from GWOSC's version keys; the *renames* are real (GWOSC did rename both when folding them into GWTC-3, and `fetch_gwosc_catalog_index` still parses them correctly from the feed), but the local rows carrying those IDs were fabricated, so they were deleted rather than retired. They are no longer reconciliation cases.

The mechanism is therefore retained for future genuine renames rather than for any present backlog. A run that reports zero retired, zero unresolved is the expected steady state.

### Forward-looking coverage, and the gaps that remain

The `preliminary`-tier-gets-renamed-on-promotion case is real and covered: `GW200105` and `GW200115` are exactly that pattern in GWOSC's feed, and the index parses both. Production currently holds one preliminary row, `GW250207_115645` (`O4_Discovery_Papers`), already in long form, so folding it into a future GWTC release most likely renames nothing at all.

Coverage is nonetheless conditional on GWOSC preserving the old name in a version key. It has done so for all 9 renames observable in the feed today, in both directions (`GW190412_053044` → `GW190412` shortens; `200201_203549` → `GW200201_203549` lengthens). If GWOSC ever retires an ID by publishing a fresh record with no shared version lineage, no rename evidence exists and the orphan lands in the unresolved bucket — flagged for review rather than mis-linked, which is the intended failure mode.

Two things reconciliation deliberately does not do:

- **It does not hide retired rows from the API.** `retired_at` and `superseded_by` are surfaced on `/api/gw/events`, but retired rows are still listed and still counted by `/api/gw/stats`. Filtering them would silently change endpoint behaviour; consumers that want only live events should filter on `retired_at IS NULL`.
- **It does not re-key `DESCRIPTIONS`.** A hand-written description is keyed on `superevent_id`, so a rename strands it silently. Three keys were found dead this way and re-keyed to the names GWOSC actually serves: `GW231123` → `GW231123_135430`, `GW200105` → `GW200105_162426`, `GW200115` → `GW200115_042309`. Reconciliation now logs a warning whenever it retires an ID that still has a `DESCRIPTIONS` entry, so the next occurrence is noisy instead of silent, but re-keying remains a manual step.
