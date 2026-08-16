-- Verification harness for the correlation SQL.
--
-- Runs the exact statements the Python worker issues (db.py) against a clean
-- database and asserts the resulting incident grouping. Useful as a smoke test
-- when changing the schema or a correlation rule:
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/verify_correlation.sql

\set ON_ERROR_STOP on
BEGIN;

TRUNCATE alert_incident, incidents, alerts RESTART IDENTITY CASCADE;

-- Three alerts from the same host in the same category, plus one from another
-- host, plus one in a different category on the original host.
INSERT INTO alerts (source, fingerprint, raw_payload, normalized, category, severity, status) VALUES
 ('prometheus','fp1','{"host":"web-01"}','{"host":"web-01","service":"checkout","category":"infra"}','infra','P2','classified'),
 ('prometheus','fp2','{"host":"web-01"}','{"host":"web-01","service":"checkout","category":"infra"}','infra','P1','classified'),
 ('zabbix',    'fp3','{"host":"web-01"}','{"host":"web-01","service":"checkout","category":"infra"}','infra','P3','classified'),
 ('prometheus','fp4','{"host":"db-01"}', '{"host":"db-01","service":"orders","category":"infra"}',  'infra','P2','classified'),
 ('falco',     'fp5','{"host":"web-01"}','{"host":"web-01","service":"checkout","category":"security"}','security','P1','classified');

-- Alert 1 opens an incident.
INSERT INTO incidents (title, summary, category, severity, alert_count)
VALUES ('infra issue on web-01', 'seeded by verification harness', 'infra', 'P2', 1);
INSERT INTO alert_incident (alert_id, incident_id) VALUES (1, 1);
UPDATE alerts SET status = 'correlated' WHERE id = 1;

-- Alerts 2 and 3 must find incident 1 through rule 'same-host-same-category'
-- (match_fields = ["category","host"]). This is find_matching_incident().
DO $$
DECLARE
    target BIGINT;
    a_id   BIGINT;
BEGIN
    FOREACH a_id IN ARRAY ARRAY[2::BIGINT, 3::BIGINT, 4::BIGINT, 5::BIGINT] LOOP
        SELECT i.id INTO target
          FROM incidents i
          JOIN alert_incident ai ON ai.incident_id = i.id
          JOIN alerts a          ON a.id = ai.alert_id
         WHERE i.status = 'open'
           AND i.category = (SELECT category FROM alerts WHERE id = a_id)
           AND i.updated_at > now() - (900 || ' seconds')::interval
           AND (COALESCE(a.normalized->>'category','') || '|' ||
                COALESCE(a.normalized->>'host','')) =
               (SELECT COALESCE(normalized->>'category','') || '|' ||
                       COALESCE(normalized->>'host','') FROM alerts WHERE id = a_id)
         GROUP BY i.id
         ORDER BY i.updated_at DESC
         LIMIT 1;

        IF target IS NOT NULL THEN
            INSERT INTO alert_incident (alert_id, incident_id)
            VALUES (a_id, target) ON CONFLICT DO NOTHING;
            UPDATE incidents SET alert_count = alert_count + 1 WHERE id = target;
            UPDATE incidents SET severity = (SELECT severity FROM alerts WHERE id = a_id)
             WHERE id = target
               AND (SELECT severity FROM alerts WHERE id = a_id) < severity;
        ELSE
            INSERT INTO incidents (title, summary, category, severity, alert_count)
            SELECT 'auto: ' || COALESCE(normalized->>'host','?'), 'opened by harness',
                   category, severity, 1
              FROM alerts WHERE id = a_id
            RETURNING id INTO target;
            INSERT INTO alert_incident (alert_id, incident_id) VALUES (a_id, target);
        END IF;

        UPDATE alerts SET status = 'correlated' WHERE id = a_id;
        target := NULL;
    END LOOP;
END $$;

-- Expectations:
--   3 incidents total  (web-01/infra, db-01/infra, web-01/security)
--   incident 1 holds 3 alerts and was escalated to P1 by alert 2
\echo ''
\echo '=== incidents ==='
SELECT id, category, severity, alert_count, title FROM incidents ORDER BY id;

\echo ''
\echo '=== assertions ==='
SELECT
  (SELECT count(*) FROM incidents) = 3                                   AS "3 incidents",
  (SELECT alert_count FROM incidents WHERE id = 1) = 3                   AS "incident 1 groups 3 alerts",
  (SELECT severity FROM incidents WHERE id = 1) = 'P1'                   AS "incident 1 escalated to P1",
  (SELECT count(*) FROM alerts WHERE status = 'correlated') = 5          AS "all 5 alerts correlated",
  (SELECT count(*) FROM alert_incident) = 5                              AS "5 links created";

ROLLBACK;
