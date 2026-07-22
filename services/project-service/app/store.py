from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class ProjectStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.workspaces: Dict[str, Dict[str, str]] = {}
        self.projects: Dict[str, Dict[str, Any]] = {}
        self.environments: Dict[str, List[Dict[str, Any]]] = {}
        self.device_pools: Dict[str, List[Dict[str, Any]]] = {}
        self.connection_profiles: Dict[str, List[Dict[str, Any]]] = {}
        self.members: Dict[str, List[Dict[str, str]]] = {}
        self.audit_logs: Dict[str, List[Dict[str, str]]] = {}
        self.outbox_events: List[Dict[str, Any]] = []
        if database_url is None:
            now = self.now()
            self.workspaces["workspace_demo"] = {"id": "workspace_demo", "name": "OpenKATE Demo"}
            self.projects["project_demo"] = {
                "id": "project_demo",
                "workspaceId": "workspace_demo",
                "name": "OpenKATE Demo",
                "description": "Execution Fabric demo project",
                "createdAt": now,
                "updatedAt": now,
                "archivedAt": None,
            }
            self.members["project_demo"] = [{"userId": "local-owner", "role": "owner"}]

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _memory_event(self, event_type: str, project_id: str, payload: Dict[str, Any]) -> None:
        self.outbox_events.append({"eventId": str(uuid4()), "eventType": event_type, "projectId": project_id, "occurredAt": self.now(), "payload": payload, "publishedAt": None})

    def _insert_event(self, connection: Any, event_type: str, project_id: str, payload: Dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO project_schema.outbox_events (id, event_type, project_id, payload) VALUES (%s, %s, %s, %s)",
            (str(uuid4()), event_type, project_id, Jsonb(payload)),
        )

    @staticmethod
    def project(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "name": row["name"],
            "description": row["description"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archivedAt": row["archived_at"],
        }

    @staticmethod
    def environment(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "write_policy": row["write_policy"],
            "allowed_hosts": row["allowed_hosts"],
            "account_refs": row["account_refs"],
            "data_set_refs": row["data_set_refs"],
            "secret_refs": row["secret_refs"],
        }

    def list_workspaces(self) -> List[Dict[str, Any]]:
        if self.database_url is None:
            return list(self.workspaces.values())
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT id, name FROM project_schema.workspaces ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def create_workspace(self, name: str) -> Dict[str, str]:
        workspace = {"id": f"workspace_{uuid4().hex[:12]}", "name": name}
        if self.database_url is None:
            self.workspaces[workspace["id"]] = workspace
            return workspace
        with psycopg.connect(self.database_url) as connection:
            connection.execute("INSERT INTO project_schema.workspaces (id, name) VALUES (%s, %s)", (workspace["id"], name))
        return workspace

    def list_projects(self, workspace_id: str) -> List[Dict[str, Any]]:
        if self.database_url is None:
            return [item for item in self.projects.values() if item["workspaceId"] == workspace_id]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id, workspace_id, name, description, created_at, updated_at, archived_at FROM project_schema.projects WHERE workspace_id = %s ORDER BY created_at",
                (workspace_id,),
            ).fetchall()
        return [self.project(dict(row)) for row in rows]

    def create_project(self, workspace_id: str, name: str, description: str, actor: str, role: str) -> Optional[Dict[str, Any]]:
        project_id = f"project_{uuid4().hex[:12]}"
        now = self.now()
        project = {"id": project_id, "workspaceId": workspace_id, "name": name, "description": description, "createdAt": now, "updatedAt": now, "archivedAt": None}
        if self.database_url is None:
            if workspace_id not in self.workspaces:
                return None
            self.projects[project_id] = project
            self.members[project_id] = [{"userId": actor, "role": role}]
            self.audit(project_id, actor, "project.created")
            self._memory_event("project.created.v1", project_id, {"name": name, "workspaceId": workspace_id})
            return project
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            exists = connection.execute("SELECT 1 FROM project_schema.workspaces WHERE id = %s", (workspace_id,)).fetchone()
            if exists is None:
                return None
            row = connection.execute(
                "INSERT INTO project_schema.projects (id, workspace_id, name, description) VALUES (%s, %s, %s, %s) RETURNING id, workspace_id, name, description, created_at, updated_at, archived_at",
                (project_id, workspace_id, name, description),
            ).fetchone()
            connection.execute("INSERT INTO project_schema.project_members (project_id, user_id, role) VALUES (%s, %s, %s)", (project_id, actor, role))
            self._insert_audit(connection, project_id, actor, "project.created")
            self._insert_event(connection, "project.created.v1", project_id, {"name": name, "workspaceId": workspace_id})
        return self.project(dict(row))

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        if self.database_url is None:
            return self.projects.get(project_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT id, workspace_id, name, description, created_at, updated_at, archived_at FROM project_schema.projects WHERE id = %s",
                (project_id,),
            ).fetchone()
        return self.project(dict(row)) if row else None

    def update_project(self, project_id: str, updates: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
        if self.database_url is None:
            project = self.projects.get(project_id)
            if project is None:
                return None
            project.update(updates)
            project["updatedAt"] = self.now()
            self.audit(project_id, actor, "project.updated")
            self._memory_event("project.updated.v1", project_id, {"name": project["name"], "description": project["description"]})
            return project
        current = self.get_project(project_id)
        if current is None:
            return None
        name = updates.get("name", current["name"])
        description = updates.get("description", current["description"])
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "UPDATE project_schema.projects SET name = %s, description = %s, updated_at = NOW() WHERE id = %s RETURNING id, workspace_id, name, description, created_at, updated_at, archived_at",
                (name, description, project_id),
            ).fetchone()
            self._insert_audit(connection, project_id, actor, "project.updated")
            self._insert_event(connection, "project.updated.v1", project_id, {"name": name, "description": description})
        return self.project(dict(row))

    def archive_project(self, project_id: str, actor: str) -> Optional[Dict[str, Any]]:
        if self.database_url is None:
            project = self.projects.get(project_id)
            if project is None:
                return None
            project["archivedAt"] = self.now()
            project["updatedAt"] = project["archivedAt"]
            self.audit(project_id, actor, "project.archived")
            self._memory_event("project.updated.v1", project_id, {"archivedAt": project["archivedAt"]})
            return project
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "UPDATE project_schema.projects SET archived_at = COALESCE(archived_at, NOW()), updated_at = NOW() WHERE id = %s RETURNING id, workspace_id, name, description, created_at, updated_at, archived_at",
                (project_id,),
            ).fetchone()
            if row:
                self._insert_audit(connection, project_id, actor, "project.archived")
                self._insert_event(connection, "project.updated.v1", project_id, {"archivedAt": row["archived_at"].isoformat()})
        return self.project(dict(row)) if row else None

    def create_environment(self, project_id: str, payload: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
        if self.get_project(project_id) is None:
            return None
        environment = {"id": f"env_{uuid4().hex[:12]}", **payload}
        if self.database_url is None:
            self.environments.setdefault(project_id, []).append(environment)
            self.audit(project_id, actor, "environment.created")
            self._memory_event("project.environment.created.v1", project_id, {"environmentId": environment["id"], "name": environment["name"], "baseUrl": environment["base_url"], "writePolicy": environment["write_policy"]})
            return environment
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "INSERT INTO project_schema.environments (id, project_id, name, base_url, write_policy, allowed_hosts, account_refs, data_set_refs, secret_refs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name, base_url, write_policy, allowed_hosts, account_refs, data_set_refs, secret_refs",
                (environment["id"], project_id, payload["name"], payload["base_url"], payload["write_policy"], payload["allowed_hosts"], payload["account_refs"], payload["data_set_refs"], psycopg.types.json.Jsonb(payload["secret_refs"])),
            ).fetchone()
            self._insert_audit(connection, project_id, actor, "environment.created")
            self._insert_event(connection, "project.environment.created.v1", project_id, {"environmentId": environment["id"], "name": environment["name"], "baseUrl": environment["base_url"], "writePolicy": environment["write_policy"]})
        return self.environment(dict(row))

    def list_environments(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get_project(project_id) is None:
            return None
        if self.database_url is None:
            return self.environments.get(project_id, [])
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id, name, base_url, write_policy, allowed_hosts, account_refs, data_set_refs, secret_refs FROM project_schema.environments WHERE project_id = %s ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self.environment(dict(row)) for row in rows]

    def get_environment(self, project_id: str, environment_id: str) -> Optional[Dict[str, Any]]:
        environments = self.list_environments(project_id)
        if environments is None:
            return None
        return next((item for item in environments if item["id"] == environment_id), None)

    def create_device_pool(self, project_id: str, name: str, device_ids: List[str], actor: str) -> Optional[Dict[str, Any]]:
        if self.get_project(project_id) is None:
            return None
        pool = {"id": f"device_pool_{uuid4().hex[:12]}", "name": name, "deviceIds": list(dict.fromkeys(device_ids))}
        if self.database_url is None:
            self.device_pools.setdefault(project_id, []).append(pool)
            self.audit(project_id, actor, "device_pool.created")
            return pool
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("INSERT INTO project_schema.device_pools (id, project_id, name, device_ids) VALUES (%s, %s, %s, %s) RETURNING id, name, device_ids", (pool["id"], project_id, name, pool["deviceIds"])).fetchone()
            self._insert_audit(connection, project_id, actor, "device_pool.created")
        return {"id": row["id"], "name": row["name"], "deviceIds": row["device_ids"]}

    def list_device_pools(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get_project(project_id) is None:
            return None
        if self.database_url is None:
            return self.device_pools.get(project_id, [])
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT id, name, device_ids FROM project_schema.device_pools WHERE project_id = %s ORDER BY created_at", (project_id,)).fetchall()
        return [{"id": row["id"], "name": row["name"], "deviceIds": row["device_ids"]} for row in rows]

    def create_connection_profile(self, project_id: str, payload: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
        if self.get_project(project_id) is None:
            return None
        profile = {"id": f"connection_{uuid4().hex[:12]}", "name": payload["name"], "kind": payload["kind"], "endpoint": payload["endpoint"], "secretRef": payload.get("secretRef")}
        if self.database_url is None:
            self.connection_profiles.setdefault(project_id, []).append(profile)
            self.audit(project_id, actor, "connection_profile.created")
            return profile
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("INSERT INTO project_schema.connection_profiles (id, project_id, name, kind, endpoint, secret_ref) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, name, kind, endpoint, secret_ref", (profile["id"], project_id, profile["name"], profile["kind"], profile["endpoint"], profile["secretRef"])).fetchone()
            self._insert_audit(connection, project_id, actor, "connection_profile.created")
        return {"id": row["id"], "name": row["name"], "kind": row["kind"], "endpoint": row["endpoint"], "secretRef": row["secret_ref"]}

    def list_connection_profiles(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get_project(project_id) is None:
            return None
        if self.database_url is None:
            return self.connection_profiles.get(project_id, [])
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT id, name, kind, endpoint, secret_ref FROM project_schema.connection_profiles WHERE project_id = %s ORDER BY created_at", (project_id,)).fetchall()
        return [{"id": row["id"], "name": row["name"], "kind": row["kind"], "endpoint": row["endpoint"], "secretRef": row["secret_ref"]} for row in rows]

    def get_connection_profile(self, project_id: str, profile_id: str) -> Optional[Dict[str, Any]]:
        profiles = self.list_connection_profiles(project_id)
        return next((profile for profile in profiles or [] if profile["id"] == profile_id), None)

    def update_environment(self, project_id: str, environment_id: str, updates: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
        current = self.get_environment(project_id, environment_id)
        if current is None:
            return None
        updated = {**current, **updates}
        if self.database_url is None:
            current.update(updates)
            self.audit(project_id, actor, "environment.updated")
            if "write_policy" in updates:
                self._memory_event("project.policy.changed.v1", project_id, {"environmentId": environment_id, "writePolicy": current["write_policy"]})
            return current
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "UPDATE project_schema.environments SET name = %s, base_url = %s, write_policy = %s, allowed_hosts = %s, account_refs = %s, data_set_refs = %s, secret_refs = %s WHERE id = %s AND project_id = %s RETURNING id, name, base_url, write_policy, allowed_hosts, account_refs, data_set_refs, secret_refs",
                (updated["name"], updated["base_url"], updated["write_policy"], updated["allowed_hosts"], updated["account_refs"], updated["data_set_refs"], psycopg.types.json.Jsonb(updated["secret_refs"]), environment_id, project_id),
            ).fetchone()
            if row:
                self._insert_audit(connection, project_id, actor, "environment.updated")
                if "write_policy" in updates:
                    self._insert_event(connection, "project.policy.changed.v1", project_id, {"environmentId": environment_id, "writePolicy": updated["write_policy"]})
        return self.environment(dict(row)) if row else None

    def member_role(self, project_id: str, user_id: str) -> Optional[str]:
        if self.database_url is None:
            member = next((item for item in self.members.get(project_id, []) if item["userId"] == user_id), None)
            return member["role"] if member else None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT role FROM project_schema.project_members WHERE project_id = %s AND user_id = %s", (project_id, user_id)).fetchone()
        return row["role"] if row else None

    def list_members(self, project_id: str) -> Optional[List[Dict[str, str]]]:
        if self.get_project(project_id) is None:
            return None
        if self.database_url is None:
            return self.members.get(project_id, [])
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT user_id, role FROM project_schema.project_members WHERE project_id = %s ORDER BY user_id", (project_id,)).fetchall()
        return [{"userId": row["user_id"], "role": row["role"]} for row in rows]

    def create_member(self, project_id: str, user_id: str, role: str, actor: str) -> Optional[Dict[str, str]]:
        if self.get_project(project_id) is None:
            return None
        member = {"userId": user_id, "role": role}
        if self.database_url is None:
            self.members.setdefault(project_id, []).append(member)
            self.audit(project_id, actor, "member.created")
            self._memory_event("project.member.changed.v1", project_id, {"userId": user_id, "role": role, "change": "added"})
            return member
        with psycopg.connect(self.database_url) as connection:
            connection.execute("INSERT INTO project_schema.project_members (project_id, user_id, role) VALUES (%s, %s, %s)", (project_id, user_id, role))
            self._insert_audit(connection, project_id, actor, "member.created")
            self._insert_event(connection, "project.member.changed.v1", project_id, {"userId": user_id, "role": role, "change": "added"})
        return member

    def update_member(self, project_id: str, user_id: str, role: str, actor: str) -> Optional[Dict[str, str]]:
        if self.database_url is None:
            for member in self.members.get(project_id, []):
                if member["userId"] == user_id:
                    member["role"] = role
                    self.audit(project_id, actor, "member.updated")
                    self._memory_event("project.member.changed.v1", project_id, {"userId": user_id, "role": role, "change": "updated"})
                    return member
            return None
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute("UPDATE project_schema.project_members SET role = %s WHERE project_id = %s AND user_id = %s", (role, project_id, user_id))
            if result.rowcount == 0:
                return None
            self._insert_audit(connection, project_id, actor, "member.updated")
            self._insert_event(connection, "project.member.changed.v1", project_id, {"userId": user_id, "role": role, "change": "updated"})
        return {"userId": user_id, "role": role}

    def delete_member(self, project_id: str, user_id: str, actor: str) -> bool:
        if self.database_url is None:
            members = self.members.get(project_id, [])
            remaining = [member for member in members if member["userId"] != user_id]
            if len(remaining) == len(members):
                return False
            self.members[project_id] = remaining
            self.audit(project_id, actor, "member.removed")
            self._memory_event("project.member.changed.v1", project_id, {"userId": user_id, "change": "removed"})
            return True
        with psycopg.connect(self.database_url) as connection:
            result = connection.execute("DELETE FROM project_schema.project_members WHERE project_id = %s AND user_id = %s", (project_id, user_id))
            if result.rowcount:
                self._insert_audit(connection, project_id, actor, "member.removed")
                self._insert_event(connection, "project.member.changed.v1", project_id, {"userId": user_id, "change": "removed"})
        return result.rowcount == 1

    def pending_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        if self.database_url is None:
            return [event for event in self.outbox_events if event["publishedAt"] is None][:limit]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT id, event_type, project_id, payload, occurred_at FROM project_schema.outbox_events WHERE published_at IS NULL ORDER BY occurred_at LIMIT %s", (limit,)).fetchall()
        return [{"eventId": row["id"], "eventType": row["event_type"], "projectId": row["project_id"], "occurredAt": row["occurred_at"].isoformat(), "payload": row["payload"]} for row in rows]

    def mark_published(self, event_id: str) -> None:
        if self.database_url is None:
            event = next(item for item in self.outbox_events if item["eventId"] == event_id)
            event["publishedAt"] = self.now()
            return
        with psycopg.connect(self.database_url) as connection:
            connection.execute("UPDATE project_schema.outbox_events SET published_at = NOW() WHERE id = %s", (event_id,))

    def audit(self, project_id: str, actor: str, action: str) -> None:
        self.audit_logs.setdefault(project_id, []).append({"id": str(uuid4()), "actor": actor, "action": action, "occurredAt": self.now()})

    @staticmethod
    def _insert_audit(connection: Any, project_id: str, actor: str, action: str) -> None:
        connection.execute("INSERT INTO project_schema.audit_logs (id, project_id, actor, action) VALUES (%s, %s, %s, %s)", (str(uuid4()), project_id, actor, action))

    def list_audit_logs(self, project_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get_project(project_id) is None:
            return None
        if self.database_url is None:
            return list(reversed(self.audit_logs.get(project_id, [])))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT id, actor, action, occurred_at FROM project_schema.audit_logs WHERE project_id = %s ORDER BY occurred_at DESC", (project_id,)).fetchall()
        return [{"id": row["id"], "actor": row["actor"], "action": row["action"], "occurredAt": row["occurred_at"]} for row in rows]

    def ready(self) -> bool:
        if self.database_url is None:
            return True
        try:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("SELECT 1 FROM project_schema.workspaces LIMIT 1")
            return True
        except psycopg.Error:
            return False
