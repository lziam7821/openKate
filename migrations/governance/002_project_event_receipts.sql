CREATE TABLE IF NOT EXISTS governance_schema.project_event_receipts (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  project_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  consumed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
