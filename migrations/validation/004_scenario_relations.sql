CREATE TABLE IF NOT EXISTS validation_schema.scenario_relations (
  scenario_id TEXT NOT NULL REFERENCES validation_schema.validation_scenarios(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  target TEXT NOT NULL,
  PRIMARY KEY (scenario_id, kind, target)
);
