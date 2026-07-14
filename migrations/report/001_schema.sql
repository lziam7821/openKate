CREATE SCHEMA IF NOT EXISTS report_schema;

CREATE TABLE IF NOT EXISTS report_schema.scenario_read_models (
  scenario_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  owner TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  document JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scenario_read_models_project_filter_idx
  ON report_schema.scenario_read_models (project_id, status, risk_level, owner);
