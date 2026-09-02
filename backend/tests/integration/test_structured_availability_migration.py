"""Safety checks for the raw-availability retirement migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0046_structured_availability_only.py"
    )
    spec = importlib.util.spec_from_file_location(
        "structured_availability_only_migration", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _days() -> dict[str, object]:
    days: dict[str, object] = {
        day: {"available": False, "disciplines": [], "time_windows": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    days["tuesday"] = {
        "available": True,
        "disciplines": ["running"],
        "time_windows": [{"time_of_day": "evening", "duration_minutes": 60}],
    }
    return days


def test_migration_removes_raw_source_from_valid_confirmed_schedule() -> None:
    migration = _migration()

    normalized = migration._normalized_availability(
        {
            "schema_version": 1,
            "status": "confirmed",
            "source_text": "Tuesday evening runs.",
            "days": _days(),
        }
    )

    assert normalized is not None
    assert normalized["schema_version"] == 2
    assert normalized["status"] == "confirmed"
    assert "source_text" not in normalized


def test_migration_clears_raw_only_or_invalid_availability() -> None:
    migration = _migration()

    assert migration._normalized_availability(None) is None
    assert migration._normalized_availability({"source_text": "weekends"}) is None
