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
