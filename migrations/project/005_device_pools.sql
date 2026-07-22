CREATE TABLE IF NOT EXISTS project_schema.device_pools (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_schema.projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  device_ids TEXT[] NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
