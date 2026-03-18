-- Rubin Scout Database Initialization
-- Runs on first docker-compose up

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Core objects table: one row per astronomical source
CREATE TABLE IF NOT EXISTS objects (
    oid TEXT PRIMARY KEY,
    ra DOUBLE PRECISION NOT NULL,
    dec DOUBLE PRECISION NOT NULL,
    position GEOGRAPHY(POINT, 4326),
    first_detection TIMESTAMPTZ,
    last_detection TIMESTAMPTZ,
    n_detections INTEGER DEFAULT 0,
    classification TEXT,
    classification_probability REAL,
    sub_classification TEXT,
    classifier_name TEXT,
    classifier_version TEXT,
    cross_match_catalog TEXT,
    cross_match_name TEXT,
    cross_match_type TEXT,
    cross_match_distance_arcsec REAL,
    host_galaxy_name TEXT,
    host_galaxy_redshift REAL,
    broker_source TEXT DEFAULT 'alerce',
    alert_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Detections table: individual brightness measurements over time
CREATE TABLE IF NOT EXISTS detections (
    id BIGSERIAL,
    oid TEXT NOT NULL REFERENCES objects(oid) ON DELETE CASCADE,
    candid BIGINT,
    mjd DOUBLE PRECISION NOT NULL,
    detection_time TIMESTAMPTZ NOT NULL,
    fid INTEGER,
    band TEXT,
    magpsf REAL,
    sigmapsf REAL,
    magap REAL,
    sigmagap REAL,
    ra DOUBLE PRECISION,
    dec DOUBLE PRECISION,
    isdiffpos TEXT,
    rb REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Convert detections to a hypertable for efficient time-series queries
SELECT create_hypertable('detections', 'detection_time', if_not_exists => TRUE);

-- Classification probabilities: full probability vector per object
CREATE TABLE IF NOT EXISTS classification_probabilities (
    id SERIAL PRIMARY KEY,
    oid TEXT NOT NULL REFERENCES objects(oid) ON DELETE CASCADE,
    classifier_name TEXT NOT NULL,
    classifier_version TEXT,
    class_name TEXT NOT NULL,
    probability REAL NOT NULL,
    ranking INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(oid, classifier_name, class_name)
);

-- Subscriptions: user notification preferences
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    user_email TEXT NOT NULL,
    filter_config JSONB NOT NULL DEFAULT '{}',
    notification_method TEXT DEFAULT 'email',
    webhook_url TEXT,
    slack_channel TEXT,
    active BOOLEAN DEFAULT TRUE,
    last_notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- GW events: gravitational wave events for cross-matching
CREATE TABLE IF NOT EXISTS gw_events (
    superevent_id TEXT PRIMARY KEY,
    event_time TIMESTAMPTZ,
    far DOUBLE PRECISION,
    skymap_url TEXT,
    classification JSONB,
    properties JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- GW candidates: optical counterpart candidates for GW events
CREATE TABLE IF NOT EXISTS gw_candidates (
    id SERIAL PRIMARY KEY,
    superevent_id TEXT NOT NULL REFERENCES gw_events(superevent_id),
    oid TEXT NOT NULL REFERENCES objects(oid),
    probability_in_skymap REAL,
    distance_to_peak_arcsec REAL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(superevent_id, oid)
);

-- Ingestion log: track what we've already processed
CREATE TABLE IF NOT EXISTS ingestion_log (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    query_params JSONB,
    objects_ingested INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT DEFAULT 'running',
    error_message TEXT
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_objects_position ON objects USING GIST(position);
CREATE INDEX IF NOT EXISTS idx_objects_classification ON objects(classification);
CREATE INDEX IF NOT EXISTS idx_objects_last_detection ON objects(last_detection DESC);
CREATE INDEX IF NOT EXISTS idx_objects_broker ON objects(broker_source);
CREATE INDEX IF NOT EXISTS idx_detections_oid ON detections(oid, detection_time DESC);
CREATE INDEX IF NOT EXISTS idx_detections_mjd ON detections(mjd DESC);
CREATE INDEX IF NOT EXISTS idx_class_probs_oid ON classification_probabilities(oid);
CREATE INDEX IF NOT EXISTS idx_subscriptions_active ON subscriptions(active) WHERE active = TRUE;

-- Helper function: convert MJD to timestamp
CREATE OR REPLACE FUNCTION mjd_to_timestamp(mjd DOUBLE PRECISION)
RETURNS TIMESTAMPTZ AS $$
BEGIN
    RETURN TIMESTAMP '1858-11-17 00:00:00 UTC' + (mjd * INTERVAL '1 day');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Helper function: convert RA/Dec to PostGIS geography point
CREATE OR REPLACE FUNCTION make_sky_point(ra DOUBLE PRECISION, dec_deg DOUBLE PRECISION)
RETURNS GEOGRAPHY AS $$
BEGIN
    RETURN ST_SetSRID(ST_MakePoint(ra, dec_deg), 4326)::GEOGRAPHY;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
