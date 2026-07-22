ALTER TABLE execution_schema.execution_plans ADD COLUMN IF NOT EXISTS document JSONB;
ALTER TABLE execution_schema.execution_runs ADD COLUMN IF NOT EXISTS document JSONB;

CREATE TABLE IF NOT EXISTS execution_schema.idempotency_keys (
  key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES execution_schema.execution_runs(id)
);
