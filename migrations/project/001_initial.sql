CREATE SCHEMA IF NOT EXISTS project_schema;

CREATE TABLE IF NOT EXISTS project_schema.workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_schema.projects (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES project_schema.workspaces(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_schema.environments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_schema.projects(id),
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  write_policy TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_schema.project_members (
  project_id TEXT NOT NULL REFERENCES project_schema.projects(id),
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_schema.audit_logs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_schema.projects(id),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
