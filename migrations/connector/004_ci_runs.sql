CREATE TABLE IF NOT EXISTS connector_schema.ci_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  pull_request_id TEXT,
  commit_sha TEXT,
  targets JSONB NOT NULL,
  scenario_ids TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
