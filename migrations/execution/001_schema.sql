CREATE SCHEMA IF NOT EXISTS execution_schema;

CREATE TABLE IF NOT EXISTS execution_schema.execution_plans (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  scenario_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  version INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  variables JSONB NOT NULL DEFAULT '{}',
  timeout_ms INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS execution_schema.execution_steps (
  plan_id TEXT NOT NULL REFERENCES execution_schema.execution_plans(id),
  id TEXT NOT NULL,
  channel TEXT NOT NULL,
  action TEXT NOT NULL,
  position INTEGER NOT NULL,
  depends_on TEXT[] NOT NULL DEFAULT '{}',
  input JSONB NOT NULL DEFAULT '{}',
  save JSONB NOT NULL DEFAULT '{}',
  timeout_ms INTEGER NOT NULL,
  idempotent BOOLEAN NOT NULL DEFAULT TRUE,
  compensation TEXT,
  PRIMARY KEY (plan_id, id)
);

CREATE TABLE IF NOT EXISTS execution_schema.execution_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  scenario_version INTEGER NOT NULL,
  plan_id TEXT NOT NULL REFERENCES execution_schema.execution_plans(id),
  environment_id TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  retry_of TEXT REFERENCES execution_schema.execution_runs(id),
  deadline TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS execution_schema.step_results (
  run_id TEXT NOT NULL REFERENCES execution_schema.execution_runs(id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL,
  input_summary JSONB NOT NULL DEFAULT '{}',
  output_summary JSONB NOT NULL DEFAULT '{}',
  assertions JSONB NOT NULL DEFAULT '[]',
  evidence_refs TEXT[] NOT NULL DEFAULT '{}',
  error JSONB,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  PRIMARY KEY (run_id, step_id)
);

CREATE TABLE IF NOT EXISTS execution_schema.resource_leases (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES execution_schema.execution_runs(id),
  account_ref TEXT,
  data_set_ref TEXT,
  browser_context_id TEXT NOT NULL,
  status TEXT NOT NULL,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS execution_schema.run_variables (
  run_id TEXT NOT NULL REFERENCES execution_schema.execution_runs(id),
  name TEXT NOT NULL,
  value JSONB NOT NULL,
  sensitive BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (run_id, name)
);

CREATE TABLE IF NOT EXISTS execution_schema.outbox_events (
  id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS execution_runs_scenario_created_idx ON execution_schema.execution_runs (scenario_id, created_at DESC);
CREATE INDEX IF NOT EXISTS execution_runs_status_idx ON execution_schema.execution_runs (project_id, status);
