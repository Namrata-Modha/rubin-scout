# Where GWTC sky localizations (skymaps) actually live

This note documents where public GWTC skymap FITS files are hosted and why
`GWEvent.skymap_url` is currently stored as `None`. It is the reference for a
future skymap-ingestion effort. Verified 2026-07.

## The flat v1 catalog carries no skymap

The `GWOSC_API_URL` this module fetches (`eventapi/json/allevents`, the flat v1
catalog) does **not** carry skymaps — only masses, distance, FAR and, per event,
PE posterior HDF5 files (dcc.ligo.org) and strain data.

The GraceDB `apiweb` URL this code built previously was doubly wrong: it used the
GWOSC `commonName` (e.g. `GW170817`) as if it were a GraceDB superevent id (those
are S-prefixed, e.g. `S190814bv`), so it 404s; the authenticated G-event path
(e.g. `G298048`) returns 401 (non-public).

## GWOSC does expose skymaps — via the v2 API and Zenodo

The v1 flat catalog simply predates them.

* **v2 API:** `GET https://gwosc.org/api/v2/event-versions/{event}-v{n}/parameters`
  Each PE parameter set carries a `links` list; for GWTC-2.1 and later the
  preferred set has an entry with `"label": "skymap"` whose URL points into a
  per-catalog Zenodo tarball. (GWTC-1 events — GW150914, GW170817 — expose only a
  `posterior-samples` DCC link here; their skymaps ship in the separate GWTC-1
  release.)

* **Skymap FITS files** are bundled in per-catalog Zenodo tarballs, not per-event
  URLs. Verified current tarballs / DOIs:

  | Catalog | Tarball | Zenodo record | DOI |
  |---------|---------|---------------|-----|
  | GWTC-2.1 | `IGWN-GWTC2p1-v2-PESkyMaps.tar.gz` | 6513631 | `10.5281/zenodo.6513631` (concept `10.5281/zenodo.5117702`) |
  | GWTC-3.0 | `IGWN-GWTC3p0-v2-PESkyLocalizations.tar.gz` | 8177023 | `10.5281/zenodo.8177023` (concept `10.5281/zenodo.5546662`; older R1 `skymaps.tar.gz` is record 5546663) |
  | GWTC-4.0 | per-event PE data products on Zenodo | — | catalog DOI `10.7935/aes8-px89` (GWTC-4.1: `10.7935/6xqf-ba54`); e.g. GW231123 → `10.5281/zenodo.16053483` (reweighted `10.5281/zenodo.17014085`) |

## Warning for the ingestion effort

For GWTC-4.0 / O4a, use the **parameter estimation (PE)** skymaps, **not** the
**candidate** (low-latency) data release — the GWTC-4.0 candidate release
documents that its O4a GstLAL skymaps are not the correct skymap for the labeled
candidate.

## Why `skymap_url` is `None`

Ingesting these tarballs (download, extract, match FITS-per-event, populate
`properties.ra_center` / `dec_center` / `area_90_deg2`) is out of scope here.
Until it lands, storing `None` is preferable to a guessed or broken URL.
