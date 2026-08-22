"""Schema-level guardrails for the reversible mobile sync credential migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Table,
    UniqueConstraint,
)

from app.db.models import MobileSyncCredential


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0030_mobile_sync_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("mobile_sync_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mobile_sync_credential_schema_is_owned_and_reversible() -> None:
    table = cast(Table, MobileSyncCredential.__table__)
    names = {column.name for column in table.columns}
    unique_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    checks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    migration = _migration()
    assert migration.__file__ is not None
    migration_source = Path(migration.__file__).read_text(encoding="utf-8")

    assert names >= {
        "user_id",
        "pairing_code_hash",
        "pairing_code_expires_at",
        "device_token_hash",
        "installation_id",
        "revoked_at",
        "last_used_at",
    }
    assert unique_constraints >= {
        "uq_mobile_sync_credentials_user_id",
        "uq_mobile_sync_credentials_pairing_code_hash",
    }
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "CASCADE"
    assert any("pairing_code_hash" in str(check.sqltext) for check in checks)
    assert 'op.create_table(\n        "mobile_sync_credentials"' in migration_source
    assert 'op.drop_table("mobile_sync_credentials")' in migration_source
