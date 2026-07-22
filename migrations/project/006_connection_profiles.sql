CREATE TABLE IF NOT EXISTS project_schema.connection_profiles (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_schema.projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  secret_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
