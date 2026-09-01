"""
Unit tests for GW retired-event reconciliation.

Covers `fetch_gwosc_catalog_index` (what GWOSC still knows about, plus the
rename map harvested from its version keys) and
`GWCrossMatchService.reconcile_retired_events` (soft-retiring rows GWOSC has
stopped serving).

The reconciliation tests use a small fake session rather than AsyncMock
side_effect lists, because reconciliation issues a variable number of queries
depending on how many orphans have a documented successor. The fake routes
`execute()` by what the statement selects, so the tests do not depend on call
ordering.
"""

from datetime import datetime, timezone

import pytest

from app.enrichment.gw_crossmatch import (
    DESCRIPTIONS,
    MAX_RETIREMENT_FRACTION,
    MIN_RETIREMENT_ABSOLUTE,
    GWCrossMatchService,
    GwoscCatalogIndex,
    fetch_gwosc_catalog_index,
)
from app.models.models import GWCandidate, GWEvent

GWOSC_URL = "https://gwosc.org/eventapi/json/allevents/"


def _entry(common_name: str, catalog_tag: str = "GWTC-3-confident", version: int = 1) -> dict:
    return {
        "commonName": common_name,
        "version": version,
        "catalog.shortName": catalog_tag,
        "GPS": 1187008882.4,
        "far": 1e-7,
        "luminosity_distance": 40.0,
        "mass_1_source": 30.0,
        "mass_2_source": 25.0,
    }


# ---------------------------------------------------------------------------
# fetch_gwosc_catalog_index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catalog_index_includes_excluded_catalogs(httpx_mock):
    """known_names must span the WHOLE feed, including catalogs we refuse to
    ingest. A row we deliberately drop by tag is not an orphan -- GWOSC still
    serves it -- and diffing against the ingestable list instead would
    mis-retire every IAS-O3a / GWTC-2.1-auxiliary row in the table."""
    httpx_mock.add_response(
        method="GET",
        url=GWOSC_URL,
        json={
            "events": {
                "GWX-KEEP-v1": _entry("GWX-KEEP", "GWTC-3-confident"),
                "GWX-IAS-v1": _entry("GWX-IAS", "IAS-O3a"),
                "GWX-AUX-v1": _entry("GWX-AUX", "GWTC-2.1-auxiliary"),
            }
        },
        status_code=200,
    )

    index = await fetch_gwosc_catalog_index()

    assert index.known_names == {"GWX-KEEP", "GWX-IAS", "GWX-AUX"}
    assert not index.is_empty


@pytest.mark.asyncio
async def test_catalog_index_harvests_renames_from_version_keys(httpx_mock):
    """A version key whose name differs from the entry's commonName is GWOSC
    telling us the event was renamed.

    Uses GW200105's real version history, which remains an accurate statement
    about the GWOSC feed. Note this is a fact about GWOSC, NOT about our data:
    the local short-named GW200105 row was found to be fabricated and deleted
    in 2026-08 (docs/gw-events-data-quality.md), so this is no longer a
    reconciliation case we have -- only a faithful parsing fixture.
    """
    httpx_mock.add_response(
        method="GET",
        url=GWOSC_URL,
        json={
            "events": {
                # Real shape: v1 published as GW200105, later renamed.
                "GW200105-v1": _entry("GW200105_162426", "O3_Discovery_Papers", version=1),
                "GW200105_162426-v2": _entry("GW200105_162426", "GWTC-3-marginal", version=2),
                "GWX-STABLE-v1": _entry("GWX-STABLE"),
            }
        },
        status_code=200,
    )

    index = await fetch_gwosc_catalog_index()

    assert index.renames == {"GW200105": "GW200105_162426"}
    assert "GW200105" not in index.known_names


@pytest.mark.asyncio
async def test_catalog_index_ignores_rename_when_old_name_still_live(httpx_mock):
    """If the historical name is itself still a live event, this is not a
    retirement and must not shadow the real event."""
    httpx_mock.add_response(
        method="GET",
        url=GWOSC_URL,
        json={
            "events": {
                "GWX-A-v1": _entry("GWX-B", version=1),
                "GWX-A-v2": _entry("GWX-A", version=2),
            }
        },
        status_code=200,
    )

    index = await fetch_gwosc_catalog_index()

    assert index.renames == {}


@pytest.mark.asyncio
async def test_catalog_index_drops_ambiguous_rename(httpx_mock):
    """One historical name resolving to two current names is ambiguous and is
    dropped rather than guessed at."""
    httpx_mock.add_response(
        method="GET",
        url=GWOSC_URL,
        json={
            "events": {
                "GWX-OLD-v1": _entry("GWX-NEW1", version=1),
                "GWX-OLD-v2": _entry("GWX-NEW2", version=2),
            }
        },
        status_code=200,
    )

    index = await fetch_gwosc_catalog_index()

    assert "GWX-OLD" not in index.renames


@pytest.mark.asyncio
async def test_catalog_index_empty_when_gwosc_unreachable(httpx_mock):
    httpx_mock.add_response(method="GET", url=GWOSC_URL, status_code=503)

    index = await fetch_gwosc_catalog_index()

    assert index.is_empty
    assert index.known_names == frozenset()


# ---------------------------------------------------------------------------
# Fake session
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Routes execute() by what the statement selects, not by call order."""

    def __init__(self, events, candidates=None):
        self.events = list(events)
        self.candidates = list(candidates or [])
        self.deleted = []
        self.commits = 0

    async def execute(self, stmt):
        desc = stmt.column_descriptions[0]
        entity, name = desc.get("entity"), desc.get("name")
        where = stmt.whereclause
        target = where.right.value if where is not None else None

        if entity is GWEvent:
            return _Result(self.events)
        if entity is GWCandidate and name == "oid":
            return _Result([c.oid for c in self.candidates if c.superevent_id == target])
        if entity is GWCandidate:
            return _Result([c for c in self.candidates if c.superevent_id == target])
        return _Result([])

    async def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.candidates:
            self.candidates.remove(obj)

    async def commit(self):
        self.commits += 1


def _event(sid, retired_at=None, superseded_by=None):
    return GWEvent(
        superevent_id=sid,
        event_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        retired_at=retired_at,
        superseded_by=superseded_by,
    )


# ---------------------------------------------------------------------------
# reconcile_retired_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_retires_orphan_with_documented_successor():
    """A documented rename retires the old row and links it to the successor."""
    svc = GWCrossMatchService()
    old, new = _event("GWX-RETIRED"), _event("GWX-SUCCESSOR")
    session = _FakeSession([old, new])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert old.retired_at is not None
    assert old.superseded_by == "GWX-SUCCESSOR"
    assert new.retired_at is None  # the successor is untouched
    assert report["retired"] == [
        {"superevent_id": "GWX-RETIRED", "superseded_by": "GWX-SUCCESSOR"}
    ]
    assert report["retired_unresolved"] == []
    assert session.commits == 1


@pytest.mark.asyncio
async def test_reconcile_flags_orphan_without_successor_for_human_review():
    """No documented rename => retired but NOT linked, even when a plausible
    same-day successor exists. Time proximity is deliberately never used as
    evidence: GWOSC trigger times are stable to 0.1 s across catalog releases,
    so an hours-wide gap argues against a rename rather than for one."""
    svc = GWCrossMatchService()
    orphan = _event("GWX-RETIRED")
    session = _FakeSession([orphan, _event("GWX-SUCCESSOR")])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}), renames={}
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert orphan.retired_at is not None
    assert orphan.superseded_by is None
    assert report["retired"] == []
    assert report["retired_unresolved"] == ["GWX-RETIRED"]


@pytest.mark.asyncio
async def test_reconcile_leaves_excluded_catalog_rows_alone():
    """A row GWOSC still serves is never retired, even though ingestion drops
    it by catalog tag. This is the 43-row IAS-O3a / GWTC-2.1-auxiliary residue."""
    svc = GWCrossMatchService()
    residue = _event("GWX-IAS")
    session = _FakeSession([residue])
    index = GwoscCatalogIndex(known_names=frozenset({"GWX-IAS"}), renames={})

    report = await svc.reconcile_retired_events(session, index=index)

    assert residue.retired_at is None
    assert report["retired"] == []
    assert report["retired_unresolved"] == []


@pytest.mark.asyncio
async def test_reconcile_relinks_candidates_to_successor():
    """Locally computed candidates survive the rename."""
    svc = GWCrossMatchService()
    old, new = _event("GWX-RETIRED"), _event("GWX-SUCCESSOR")
    cand = GWCandidate(id=1, superevent_id="GWX-RETIRED", oid="ZTF1")
    session = _FakeSession([old, new], [cand])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert cand.superevent_id == "GWX-SUCCESSOR"
    assert report["candidates_relinked"] == 1
    assert report["candidates_deduped"] == 0
    assert session.deleted == []


@pytest.mark.asyncio
async def test_reconcile_dedupes_candidate_the_successor_already_has():
    """UNIQUE(superevent_id, oid) means a colliding oid cannot be re-pointed;
    the retired duplicate is dropped and the successor's row kept."""
    svc = GWCrossMatchService()
    old, new = _event("GWX-RETIRED"), _event("GWX-SUCCESSOR")
    dupe = GWCandidate(id=1, superevent_id="GWX-RETIRED", oid="ZTF1")
    kept = GWCandidate(id=2, superevent_id="GWX-SUCCESSOR", oid="ZTF1")
    session = _FakeSession([old, new], [dupe, kept])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert session.deleted == [dupe]
    assert kept.superevent_id == "GWX-SUCCESSOR"
    assert report["candidates_deduped"] == 1
    assert report["candidates_relinked"] == 0


@pytest.mark.asyncio
async def test_reconcile_skips_successor_not_yet_in_database():
    """superseded_by is a foreign key: never point at a row that is not there."""
    svc = GWCrossMatchService()
    orphan = _event("GWX-RETIRED")
    session = _FakeSession([orphan])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert orphan.retired_at is not None
    assert orphan.superseded_by is None
    assert report["retired_unresolved"] == ["GWX-RETIRED"]


@pytest.mark.asyncio
async def test_reconcile_is_idempotent():
    """A second pass must not re-report an already retired row."""
    svc = GWCrossMatchService()
    old, new = _event("GWX-RETIRED"), _event("GWX-SUCCESSOR")
    session = _FakeSession([old, new])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    await svc.reconcile_retired_events(session, index=index)
    first_retired_at = old.retired_at
    second = await svc.reconcile_retired_events(session, index=index)

    assert old.retired_at == first_retired_at  # not bumped
    assert second["retired"] == []
    assert second["retired_unresolved"] == []


@pytest.mark.asyncio
async def test_reconcile_resolves_successor_on_a_later_pass():
    """A row retired earlier without a successor picks one up once GWOSC
    publishes the rename, without being re-reported as newly retired."""
    svc = GWCrossMatchService()
    old = _event("GWX-RETIRED", retired_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new = _event("GWX-SUCCESSOR")
    session = _FakeSession([old, new])
    index = GwoscCatalogIndex(
        known_names=frozenset({"GWX-SUCCESSOR"}),
        renames={"GWX-RETIRED": "GWX-SUCCESSOR"},
    )

    report = await svc.reconcile_retired_events(session, index=index)

    assert old.superseded_by == "GWX-SUCCESSOR"
    assert report["retired"] == []  # not newly retired


@pytest.mark.asyncio
async def test_reconcile_unretires_event_that_reappears():
    """GWOSC serving an ID again clears the retirement."""
    svc = GWCrossMatchService()
    back = _event(
        "GWX-BACK",
        retired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        superseded_by=None,
    )
    session = _FakeSession([back])
    index = GwoscCatalogIndex(known_names=frozenset({"GWX-BACK"}), renames={})

    report = await svc.reconcile_retired_events(session, index=index)

    assert back.retired_at is None
    assert report["unretired"] == ["GWX-BACK"]


@pytest.mark.asyncio
async def test_reconcile_no_ops_when_gwosc_unavailable():
    """An empty index means no information, NOT 'everything was retired'."""
    svc = GWCrossMatchService()
    row = _event("GWX-LIVE")
    session = _FakeSession([row])

    report = await svc.reconcile_retired_events(
        session, index=GwoscCatalogIndex(frozenset(), {})
    )

    assert row.retired_at is None
    assert report["skipped_reason"] == "gwosc_unavailable"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_reconcile_refuses_implausibly_large_diff():
    """A truncated feed must not mass-retire a healthy table."""
    svc = GWCrossMatchService()
    rows = [_event(f"GWX-{i}") for i in range(100)]
    session = _FakeSession(rows)
    # Only one of a hundred survives => 99% would be retired, far over the cap.
    index = GwoscCatalogIndex(known_names=frozenset({"GWX-0"}), renames={})

    report = await svc.reconcile_retired_events(session, index=index)

    assert report["skipped_reason"] == "implausible_diff"
    assert all(r.retired_at is None for r in rows)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_reconcile_allows_diff_at_the_fraction_boundary():
    """Exactly at the cap is allowed; the guard trips on strictly greater."""
    svc = GWCrossMatchService()
    rows = [_event(f"GWX-{i}") for i in range(100)]
    session = _FakeSession(rows)
    survivors = {f"GWX-{i}" for i in range(80)}  # 20 retired == 20.0%
    index = GwoscCatalogIndex(known_names=frozenset(survivors), renames={})

    report = await svc.reconcile_retired_events(session, index=index)

    assert 20 > MIN_RETIREMENT_ABSOLUTE  # the floor is not what let this pass
    assert 20 == 100 * MAX_RETIREMENT_FRACTION
    assert report["skipped_reason"] is None
    assert len(report["retired_unresolved"]) == 20


@pytest.mark.asyncio
async def test_reconcile_small_absolute_diff_bypasses_fraction_guard():
    """On a small table a single legitimate retirement exceeds the fraction
    but must still go through -- that is what MIN_RETIREMENT_ABSOLUTE is for."""
    svc = GWCrossMatchService()
    rows = [_event("GWX-GONE"), _event("GWX-LIVE")]
    session = _FakeSession(rows)
    index = GwoscCatalogIndex(known_names=frozenset({"GWX-LIVE"}), renames={})

    report = await svc.reconcile_retired_events(session, index=index)

    assert 1 / 2 > MAX_RETIREMENT_FRACTION  # fraction alone would have blocked
    assert report["skipped_reason"] is None
    assert report["retired_unresolved"] == ["GWX-GONE"]


# ---------------------------------------------------------------------------
# DESCRIPTIONS keys must track GWOSC naming
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "dead_short_key, live_long_key",
    [
        # GWOSC has only ever served this one as the long name (version keys
        # GW231123_135430-v1..v3); the bare name never appeared in the feed.
        ("GW231123", "GW231123_135430"),
        # These two were published short under O3_Discovery_Papers, then
        # renamed when folded into GWTC-3.
        ("GW200105", "GW200105_162426"),
        ("GW200115", "GW200115_042309"),
    ],
)
def test_descriptions_keyed_on_names_gwosc_actually_serves(dead_short_key, live_long_key):
    """A description keyed on a name GWOSC does not serve is never rendered.

    Each short key here matched nothing in the live feed, so its text was dead
    weight; the long form is what ingestion actually stores a row under.
    """
    assert dead_short_key not in DESCRIPTIONS
    assert live_long_key in DESCRIPTIONS


@pytest.mark.asyncio
async def test_reconcile_warns_when_retiring_a_described_event(caplog):
    """Retiring an ID that still has a hand-written DESCRIPTIONS entry must
    warn -- nothing else surfaces a stranded description key."""
    svc = GWCrossMatchService()
    described = next(iter(DESCRIPTIONS))
    orphan = _event(described)
    session = _FakeSession([orphan])
    index = GwoscCatalogIndex(known_names=frozenset({"GWX-OTHER"}), renames={})

    with caplog.at_level("WARNING"):
        await svc.reconcile_retired_events(session, index=index)

    assert any(
        "DESCRIPTIONS" in r.message and described in str(r.args)
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_reconcile_does_not_warn_for_live_described_events(caplog):
    """No warning when the described event is still served."""
    svc = GWCrossMatchService()
    described = next(iter(DESCRIPTIONS))
    session = _FakeSession([_event(described)])
    index = GwoscCatalogIndex(known_names=frozenset({described}), renames={})

    with caplog.at_level("WARNING"):
        await svc.reconcile_retired_events(session, index=index)

    assert not any("DESCRIPTIONS" in r.message for r in caplog.records)
