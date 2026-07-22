CREATE TABLE IF NOT EXISTS governance_schema.failure_classifications (
  failure_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  audit JSONB NOT NULL DEFAULT '[]'
);
