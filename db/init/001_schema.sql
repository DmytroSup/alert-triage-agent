-- alert-triage-agent :: schema
-- PostgreSQL 16

CREATE TYPE alert_status  AS ENUM ('new', 'processing', 'classified', 'correlated', 'failed');
CREATE TYPE alert_category AS ENUM ('infra', 'security', 'application', 'network', 'unknown');
CREATE TYPE severity_level AS ENUM ('P1', 'P2', 'P3', 'P4');
CREATE TYPE incident_status AS ENUM ('open', 'acknowledged', 'closed');

-- Raw alerts land here untouched. Everything the worker derives is stored
-- alongside the original payload so a bad classification can always be replayed.
CREATE TABLE alerts (
    id             BIGSERIAL PRIMARY KEY,
    source         TEXT           NOT NULL,
    fingerprint    TEXT,
    raw_payload    JSONB          NOT NULL,
    normalized     JSONB,
    status         alert_status   NOT NULL DEFAULT 'new',
    category       alert_category,
    severity       severity_level,
    classification_reason TEXT,
    received_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ
);

CREATE INDEX idx_alerts_status       ON alerts (status);
CREATE INDEX idx_alerts_received_at  ON alerts (received_at DESC);
CREATE INDEX idx_alerts_fingerprint  ON alerts (fingerprint);
CREATE INDEX idx_alerts_raw_payload  ON alerts USING GIN (raw_payload);

-- An incident is the unit a human actually looks at.
CREATE TABLE incidents (
    id           BIGSERIAL PRIMARY KEY,
    title        TEXT             NOT NULL,
    summary      TEXT,
    category     alert_category   NOT NULL DEFAULT 'unknown',
    severity     severity_level   NOT NULL DEFAULT 'P4',
    status       incident_status  NOT NULL DEFAULT 'open',
    alert_count  INT              NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ
);

CREATE INDEX idx_incidents_status     ON incidents (status);
CREATE INDEX idx_incidents_created_at ON incidents (created_at DESC);
CREATE INDEX idx_incidents_open_match ON incidents (status, category, severity)
    WHERE status = 'open';

-- Many-to-many: one alert belongs to exactly one incident today, but the join
-- table keeps the door open for re-correlation without destroying history.
CREATE TABLE alert_incident (
    alert_id    BIGINT NOT NULL REFERENCES alerts(id)    ON DELETE CASCADE,
    incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    linked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_id, incident_id)
);

CREATE INDEX idx_alert_incident_incident ON alert_incident (incident_id);

-- Correlation is data, not code: rules live in the database so behaviour can be
-- tuned without a redeploy.
CREATE TABLE correlation_rules (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT        NOT NULL UNIQUE,
    time_window_sec INT         NOT NULL DEFAULT 900,
    match_fields    JSONB       NOT NULL DEFAULT '["category"]'::jsonb,
    enabled         BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO correlation_rules (name, time_window_sec, match_fields) VALUES
    ('same-host-same-category', 900,  '["category", "host"]'::jsonb),
    ('same-service-any',        1800, '["service"]'::jsonb),
    ('category-wide-fallback',  600,  '["category"]'::jsonb);

-- Keep incidents.updated_at honest without application-side bookkeeping.
CREATE OR REPLACE FUNCTION touch_incident_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_incidents_touch
    BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION touch_incident_updated_at();
