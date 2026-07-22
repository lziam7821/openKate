CREATE TABLE IF NOT EXISTS governance_schema.business_rules (
  id TEXT PRIMARY KEY,
  badcase_id TEXT NOT NULL REFERENCES governance_schema.badcases(id),
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  active_version INTEGER,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS governance_schema.rule_versions (
  rule_id TEXT NOT NULL REFERENCES governance_schema.business_rules(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  source_badcase_id TEXT NOT NULL REFERENCES governance_schema.badcases(id),
  scope JSONB NOT NULL,
  expected_effect TEXT NOT NULL,
  content TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE IF NOT EXISTS governance_schema.approvals (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES governance_schema.business_rules(id) ON DELETE CASCADE,
  rule_version INTEGER NOT NULL,
  approver TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (rule_id, rule_version, approver)
);

CREATE TABLE IF NOT EXISTS governance_schema.rule_evaluations (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES governance_schema.business_rules(id) ON DELETE CASCADE,
  rule_version INTEGER NOT NULL,
  run_ids JSONB NOT NULL,
  new_hits INTEGER NOT NULL,
  false_positives INTEGER NOT NULL,
  false_negatives INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
