ALTER TABLE project_schema.projects
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
