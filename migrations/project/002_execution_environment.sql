ALTER TABLE project_schema.environments ADD COLUMN IF NOT EXISTS allowed_hosts TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE project_schema.environments ADD COLUMN IF NOT EXISTS account_refs TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE project_schema.environments ADD COLUMN IF NOT EXISTS data_set_refs TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE project_schema.environments ADD COLUMN IF NOT EXISTS secret_refs JSONB NOT NULL DEFAULT '{}';
