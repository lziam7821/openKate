DO $$
DECLARE
  service_name TEXT;
  schema_name TEXT;
  migration_role TEXT;
  runtime_role TEXT;
BEGIN
  FOREACH service_name IN ARRAY ARRAY['agent', 'asset', 'connector', 'execution', 'governance', 'project', 'report', 'validation', 'workflow']
  LOOP
    schema_name := service_name || '_schema';
    migration_role := service_name || '_migration';
    runtime_role := service_name || '_runtime';

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = migration_role) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', migration_role, 'change-me');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', runtime_role, 'change-me');
    END IF;

    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', schema_name, migration_role);
    EXECUTE format('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I TO %I', schema_name, migration_role);
    EXECUTE format('GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I TO %I', schema_name, migration_role);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', schema_name, runtime_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', schema_name, runtime_role);
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', schema_name, runtime_role);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', schema_name, runtime_role);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I', schema_name, runtime_role);
  END LOOP;
END $$;
