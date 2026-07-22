ALTER TABLE governance_schema.rule_evaluations ADD COLUMN IF NOT EXISTS run_snapshot JSONB NOT NULL DEFAULT '[]';
