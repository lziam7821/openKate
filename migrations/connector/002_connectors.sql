CREATE TABLE IF NOT EXISTS connector_schema.connectors (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  repository TEXT NOT NULL,
  secret_ref TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS connector_schema.webhook_deliveries (
  id TEXT PRIMARY KEY,
  connector_id TEXT NOT NULL REFERENCES connector_schema.connectors(id) ON DELETE CASCADE,
  delivery_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (connector_id, delivery_id)
);
