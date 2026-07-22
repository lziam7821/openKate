CREATE TABLE IF NOT EXISTS validation_schema.event_outbox (
  sequence BIGSERIAL PRIMARY KEY,
  event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL,
  project_id TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS validation_event_outbox_sequence_idx
  ON validation_schema.event_outbox (sequence);
