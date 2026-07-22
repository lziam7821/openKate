CREATE TABLE IF NOT EXISTS connector_schema.pull_requests (
  id TEXT PRIMARY KEY,
  connector_id TEXT NOT NULL REFERENCES connector_schema.connectors(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL,
  delivery_id TEXT NOT NULL,
  number INTEGER NOT NULL,
  action TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
