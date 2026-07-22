ALTER TABLE governance_schema.badcases ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE governance_schema.business_rules ADD COLUMN IF NOT EXISTS project_id TEXT;
CREATE INDEX IF NOT EXISTS business_rules_project_published_idx ON governance_schema.business_rules (project_id) WHERE status = 'published';
