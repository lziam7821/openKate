CREATE SCHEMA IF NOT EXISTS validation_schema;

CREATE TABLE IF NOT EXISTS validation_schema.requirements (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS validation_schema.validation_scenarios (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  owner TEXT NOT NULL,
  current_version INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS validation_scenarios_project_status_risk_idx
  ON validation_schema.validation_scenarios (project_id, status, risk_level);

CREATE TABLE IF NOT EXISTS validation_schema.scenario_versions (
  scenario_id TEXT NOT NULL REFERENCES validation_schema.validation_scenarios(id),
  version INTEGER NOT NULL,
  content JSONB NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (scenario_id, version)
);

CREATE TABLE IF NOT EXISTS validation_schema.risks (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL REFERENCES validation_schema.validation_scenarios(id),
  scenario_version INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_schema.evidence_points (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL REFERENCES validation_schema.validation_scenarios(id),
  scenario_version INTEGER NOT NULL,
  channel TEXT NOT NULL,
  target TEXT NOT NULL,
  observation TEXT NOT NULL,
  assertions JSONB NOT NULL,
  required BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS validation_schema.reviews (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL REFERENCES validation_schema.validation_scenarios(id),
  scenario_version INTEGER NOT NULL,
  author TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
