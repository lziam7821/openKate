CREATE TABLE IF NOT EXISTS connector_schema.sync_cursors (
  connector_id TEXT PRIMARY KEY REFERENCES connector_schema.connectors(id) ON DELETE CASCADE,
  cursor TEXT,
  status TEXT NOT NULL,
  synchronized_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
