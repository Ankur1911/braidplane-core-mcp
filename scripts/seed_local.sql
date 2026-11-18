-- Synthetic fixtures for a DISPOSABLE local Core database only.
-- Core's own seeder (`fake --core-only`) creates org / project / API key but no
-- pipelines or runs (those normally need a Git backend and connector services),
-- so this file inserts three pipelines and a few runs directly:
--   orders-daily-sync    healthy: recent successful run
--   customer-enrichment  latest run FAILED (source_query_failed), earlier run ok
--   inventory-export     STALE: last success days ago, 24h recency expectation
-- Usage: psql -v org=<org-slug> -v project=<project-slug> -f seed_local.sql
\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE ctx AS
SELECT p.id AS project_id, pe.id AS env_id, e.slug AS env_slug
FROM projects p
JOIN organizations o ON o.id = p.organization_id
JOIN project_environments pe ON pe.project_id = p.id
JOIN environments e ON e.id = pe.environment_id
WHERE o.slug = :'org' AND p.slug = :'project' AND e.slug = 'dev';

INSERT INTO pipeline_specs (project_id, slug, name, spec_jsonb, spec_hash, source_path, source_ref, last_applied_commit)
SELECT project_id, s.slug, s.name,
       jsonb_build_object('metadata', jsonb_build_object('slug', s.slug, 'name', s.name), 'steps', '[]'::jsonb),
       'hash-' || s.slug, 'pipelines/' || s.slug || '.pipeline.json', 'main', 'seed0000000000000000000000000000000000'
FROM ctx, (VALUES ('orders-daily-sync','Orders daily sync'),
                  ('customer-enrichment','Customer enrichment'),
                  ('inventory-export','Inventory export')) AS s(slug, name);

INSERT INTO pipeline_versions (project_id, pipeline_slug, git_commit_sha, spec_jsonb, spec_hash, source_path)
SELECT project_id, slug, last_applied_commit, spec_jsonb, spec_hash, source_path
FROM pipeline_specs WHERE project_id = (SELECT project_id FROM ctx);

UPDATE pipeline_specs ps SET current_version_id = v.id
FROM pipeline_versions v WHERE v.project_id = ps.project_id AND v.pipeline_slug = ps.slug;

INSERT INTO pipeline_references (project_id, slug, name, current_version_id)
SELECT project_id, slug, name, current_version_id FROM pipeline_specs
WHERE project_id = (SELECT project_id FROM ctx);

INSERT INTO pipeline_deployments (project_environment_id, pipeline_slug, current_version_id, deployed_by_actor_type, project_id, deployed_at, updated_at)
SELECT c.env_id, ps.slug, ps.current_version_id, 'user', c.project_id, now() - interval '6 days', now() - interval '6 days'
FROM ctx c JOIN pipeline_specs ps ON ps.project_id = c.project_id;

-- Runs: (pipeline, run slug, status, age of start, duration ms, failure kind, error summary, step)
CREATE TEMP TABLE seed_runs (pipeline text, run text, status text, age interval, dur bigint, kind text, side text, err text, step text);
INSERT INTO seed_runs VALUES
 ('orders-daily-sync',   'orders-daily-sync-r3',   'succeeded', interval '2 hours',  4200,  NULL, NULL, NULL, 'persist-orders'),
 ('orders-daily-sync',   'orders-daily-sync-r2',   'succeeded', interval '26 hours', 4100,  NULL, NULL, NULL, 'persist-orders'),
 ('customer-enrichment', 'customer-enrichment-r3', 'failed',    interval '3 hours',  1800,  'data_source', 'source',
     'Source query failed: relation "crm.customers" does not exist (step fetch-customers)', 'fetch-customers'),
 ('customer-enrichment', 'customer-enrichment-r2', 'failed',    interval '27 hours', 1750,  'data_source', 'source',
     'Source query failed: relation "crm.customers" does not exist (step fetch-customers)', 'fetch-customers'),
 ('customer-enrichment', 'customer-enrichment-r1', 'succeeded', interval '51 hours', 5300,  NULL, NULL, NULL, 'persist-segments'),
 ('inventory-export',    'inventory-export-r2',    'succeeded', interval '5 days',   9100,  NULL, NULL, NULL, 'write-export'),
 ('inventory-export',    'inventory-export-r1',    'succeeded', interval '6 days',   9000,  NULL, NULL, NULL, 'write-export');

INSERT INTO pipeline_runs (project_environment_id, pipeline_version_id, pipeline_version_spec_hash, api_version, pipeline_slug, run_slug,
    source_type, source_summary, source_entrypoint, source_actor_type, source_environment_slug, status, duration_ms, step_count, step_slug,
    error_summary, failure_kind, failure_side, started_at, ended_at, timeline, project_id, source_root_run_slug, deployment_id, deployment_revision)
SELECT c.env_id, ps.current_version_id, ps.spec_hash, 'v1', r.pipeline, r.run,
    'schedule', 'Scheduled run', 'scheduler', 'system', c.env_slug, r.status, r.dur, 2, r.step,
    r.err, r.kind, r.side, now() - r.age, now() - r.age + (r.dur || ' milliseconds')::interval,
    jsonb_build_object(
      'run_slug', r.run, 'pipeline_slug', r.pipeline, 'project_slug', :'project', 'environment_slug', c.env_slug,
      'status', r.status,
      'events', jsonb_build_array(
        jsonb_build_object('at', now() - r.age, 'type', 'run_started', 'status', 'running'),
        jsonb_build_object('at', now() - r.age + interval '100 milliseconds', 'type', 'step_started', 'step', r.step, 'status', 'running'),
        jsonb_build_object('at', now() - r.age + (r.dur || ' milliseconds')::interval, 'type', 'step_finished', 'step', r.step,
                           'status', CASE WHEN r.status = 'succeeded' THEN 'succeeded' ELSE 'failed' END),
        jsonb_build_object('at', now() - r.age + (r.dur || ' milliseconds')::interval, 'type', 'run_finished', 'status', r.status)),
      'snapshots', '[]'::jsonb)
      || CASE WHEN r.status = 'failed' THEN jsonb_build_object('failure', jsonb_build_object(
          'step', r.step, 'error', r.err, 'at', now() - r.age + (r.dur || ' milliseconds')::interval,
          'kind', r.kind, 'side', r.side, 'reason_code', 'source_query_failed',
          'action_hint', 'Check that the source table exists in the connected database, then run the pipeline again.')) ELSE '{}'::jsonb END,
    c.project_id, r.run, d.id, d.revision
FROM seed_runs r
JOIN ctx c ON true
JOIN pipeline_specs ps ON ps.project_id = c.project_id AND ps.slug = r.pipeline
JOIN pipeline_deployments d ON d.project_environment_id = c.env_id AND d.pipeline_slug = r.pipeline;

-- Recency evidence + a 24h expectation on every pipeline.
INSERT INTO pipeline_run_recency_successes (project_environment_id, pipeline_slug, deployment_id, deployment_revision, pipeline_version_id, run_slug, succeeded_at)
SELECT DISTINCT ON (r.pipeline_slug) r.project_environment_id, r.pipeline_slug, r.deployment_id, r.deployment_revision, r.pipeline_version_id, r.run_slug, r.ended_at
FROM pipeline_runs r WHERE r.status = 'succeeded' AND r.project_id = (SELECT project_id FROM ctx)
ORDER BY r.pipeline_slug, r.ended_at DESC;

INSERT INTO pipeline_run_recency_expectations (project_environment_id, pipeline_slug, revision, maximum_age_seconds, enabled, enabled_at)
SELECT c.env_id, ps.slug, 1, 86400, true, now() - interval '7 days'
FROM ctx c JOIN pipeline_specs ps ON ps.project_id = c.project_id;

COMMIT;
