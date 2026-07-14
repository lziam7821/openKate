CREATE TABLE IF NOT EXISTS project_schema.outbox_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  project_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS project_outbox_pending_idx
  ON project_schema.outbox_events (occurred_at)
  WHERE published_at IS NULL;
