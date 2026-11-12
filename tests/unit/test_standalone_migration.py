from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from knowledge_vault import migration


def test_migration_holds_lock_on_alembic_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    connection.execute.side_effect = [
        SimpleNamespace(scalar_one=lambda: True),
        SimpleNamespace(scalar_one=lambda: True),
    ]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(migration, "create_engine", lambda *a, **kw: engine)
    checked = []

    def upgrade(config, revision):
        assert revision == "head"
        assert config.attributes["connection"] is connection
        # Acquiring the session lock has been committed before Alembic starts.
        assert connection.commit.call_count == 1
        checked.append(True)

    monkeypatch.setattr(migration.command, "upgrade", upgrade)
    heads = migration.ScriptDirectory.from_config(migration.Config("alembic.ini")).get_heads()
    monkeypatch.setattr(
        migration.MigrationContext,
        "configure",
        lambda conn: SimpleNamespace(get_current_heads=lambda: tuple(heads)),
    )
    migration.migrate("postgresql://fixture.invalid/vault")
    assert checked == [True]
    assert connection.execute.call_count == 2
    engine.dispose.assert_called_once()


def test_busy_migration_does_not_call_alembic(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = False
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(migration, "create_engine", lambda *a, **kw: engine)
    upgrade = MagicMock()
    monkeypatch.setattr(migration.command, "upgrade", upgrade)
    with pytest.raises(migration.MigrationUnavailable, match="another standalone migration"):
        migration.migrate("postgresql://fixture.invalid/vault")
    upgrade.assert_not_called()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("actual", [(), ("unexpected",), ("expected", "unexpected")])
def test_revision_mismatch_fails_and_closes_engine(
    monkeypatch: pytest.MonkeyPatch, actual: tuple[str, ...]
) -> None:
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = True
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(migration, "create_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(
        migration.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["expected"]),
    )
    monkeypatch.setattr(migration.command, "upgrade", lambda config, revision: None)
    monkeypatch.setattr(
        migration.MigrationContext,
        "configure",
        lambda conn: SimpleNamespace(get_current_heads=lambda: actual),
    )
    with pytest.raises(migration.MigrationUnavailable, match="does not match"):
        migration.migrate("postgresql://fixture.invalid/vault")
    engine.dispose.assert_called_once()


def test_multiple_shipped_heads_fail_before_database_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration.ScriptDirectory,
        "from_config",
        lambda config: SimpleNamespace(get_heads=lambda: ["first", "second"]),
    )
    create = MagicMock()
    monkeypatch.setattr(migration, "create_engine", create)
    with pytest.raises(migration.MigrationUnavailable, match="exactly one head"):
        migration.migrate("postgresql://fixture.invalid/vault")
    create.assert_not_called()


def test_cli_failure_does_not_print_exception_or_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "synthetic-private-value"
    monkeypatch.setattr(migration, "Settings", lambda: SimpleNamespace(database_url=marker))

    def fail(_url):
        raise RuntimeError(marker)

    monkeypatch.setattr(migration, "migrate", fail)
    with pytest.raises(SystemExit) as exc:
        migration.run_migration()
    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert exc.value.__context__ is None
    assert marker not in captured.out + captured.err
    assert "Standalone migration failed" in captured.err
