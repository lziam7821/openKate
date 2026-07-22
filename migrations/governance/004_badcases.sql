CREATE TABLE IF NOT EXISTS governance_schema.badcases (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  evidence_refs JSONB NOT NULL,
  description TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
