from pathlib import Path


def test_each_service_has_isolated_migration_and_runtime_roles() -> None:
    sql = (Path(__file__).parents[1] / "migrations" / "zz_permissions" / "001_service_roles.sql").read_text()
    for service in ("agent", "asset", "connector", "execution", "governance", "project", "report", "validation", "workflow"):
        assert service in sql
    assert "GRANT USAGE ON SCHEMA" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in sql
    assert "GRANT ALL PRIVILEGES ON ALL TABLES" in sql
