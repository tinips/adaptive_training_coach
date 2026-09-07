"""Default values for import-path settings.

Screenshot capture is the default-enabled import path; TCX import is
available but default-disabled. See CLAUDE.md's import-path notes.
"""

from __future__ import annotations

from app.config import Settings


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
    )


def test_screenshot_import_defaults_enabled() -> None:
    assert _settings().screenshot_import_enabled is True


def test_tcx_import_defaults_disabled() -> None:
    assert _settings().tcx_import_enabled is False


def test_tcx_import_remains_toggleable() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        tcx_import_enabled=True,
    )

    assert settings.tcx_import_enabled is True


def test_exposed_configuration_reports_both_import_paths() -> None:
    exposed = _settings().exposed_configuration()

    assert exposed["screenshot_import_enabled"] is True
    assert exposed["tcx_import_enabled"] is False
