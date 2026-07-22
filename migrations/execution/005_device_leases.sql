ALTER TABLE execution_schema.resource_leases ADD COLUMN IF NOT EXISTS device_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS active_device_lease_idx ON execution_schema.resource_leases (device_id) WHERE status = 'active' AND device_id IS NOT NULL;
