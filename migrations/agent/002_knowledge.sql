CREATE TABLE IF NOT EXISTS agent_schema.knowledge_items (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL,
  category TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_schema.knowledge_embeddings (
  knowledge_id TEXT PRIMARY KEY REFERENCES agent_schema.knowledge_items(id) ON DELETE CASCADE,
  vector JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_schema.knowledge_snapshots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  query TEXT NOT NULL,
  knowledge_ids JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
