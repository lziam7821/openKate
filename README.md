# OpenKATE

OpenKATE is a multi-service business validation platform. The v0.1 foundation provides OIDC authentication, workspaces, projects, secured environments, project members, audit logs, health reporting, and transactional project events.

## Local development

Prerequisites: Python 3.9+, pnpm 11+, and Docker with Compose.

```bash
make bootstrap
make lint
make test
make up
```

`make up` enables the `core`, `ai`, `executors`, and `observability` Compose profiles. It applies PostgreSQL migrations, creates the MinIO artifact bucket, starts NATS JetStream and Temporal, then starts the Gateway, nine domain services, three executors, the Web console, and the telemetry collector.

Set these values before using a real identity provider:

```bash
export OPENKATE_JWT_SECRET='replace-with-at-least-32-random-bytes'
export OPENKATE_OIDC_ISSUER='https://identity.example.com'
export OPENKATE_OIDC_AUDIENCE='openkate'
```

The Web build uses `VITE_OIDC_AUTHORITY`, `VITE_OIDC_CLIENT_ID`, and optionally `VITE_OIDC_REDIRECT_URI`. The v0.1 administration console is served at `/foundation`.

## Verification

```bash
make lint  # Ruff, mypy, ESLint
make test  # pytest, Vitest, TypeScript, Vite production build
```

Project changes write their event to `project_schema.outbox_events` in the same database transaction. `project-outbox-publisher` publishes pending events to the `OPENKATE_EVENTS` JetStream stream; consumers use event IDs for idempotency and send exhausted deliveries to `openkate.dlq.*`.
