ALTER TABLE validation_schema.validation_scenarios
  ADD COLUMN IF NOT EXISTS aggregate JSONB;

CREATE INDEX IF NOT EXISTS validation_scenarios_updated_idx
  ON validation_schema.validation_scenarios (project_id, updated_at DESC);
