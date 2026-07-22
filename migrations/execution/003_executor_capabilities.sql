CREATE TABLE IF NOT EXISTS execution_schema.executor_capabilities (
  project_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  worker TEXT,
  capabilities JSONB NOT NULL DEFAULT '[]',
  sdk_version TEXT,
  contract_version TEXT,
  status TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (project_id, channel)
);
