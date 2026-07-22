CREATE TABLE IF NOT EXISTS execution_schema.external_callbacks (
  id TEXT PRIMARY KEY,
  callback_token TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS external_callbacks_token_received_idx ON execution_schema.external_callbacks (callback_token, received_at);
