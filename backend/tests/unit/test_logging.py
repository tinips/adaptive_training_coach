"""Secret-redaction tests, including Uvicorn-style access records."""

from __future__ import annotations

import io
import logging

from app.logging import SensitiveValueFilter, configure_logging


def test_uvicorn_access_tuple_redacts_sensitive_query_values() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:5000",
            "GET",
            "/integrations/callback?state=raw-state&code=raw-code"
            "&hub.verify_token=raw-verify&scope=read",
            "1.1",
            200,
        ),
        exc_info=None,
    )

    assert SensitiveValueFilter().filter(record)
    rendered = record.getMessage()
    assert "raw-state" not in rendered
    assert "raw-code" not in rendered
    assert "raw-verify" not in rendered
    assert "state=<redacted>" in rendered
    assert "code=<redacted>" in rendered


def test_preformatted_connect_ticket_and_structured_secret_are_redacted() -> None:
    access = logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "GET /integrations/connect?ticket=raw-ticket "
            "https://api.telegram.org/botraw-telegram/sendMessage "
            "postgresql+asyncpg://coach:raw-password@localhost/db"
        ),
        args=(),
        exc_info=None,
    )
    structured = logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider context %(client_secret)s",
        args=({"client_secret": "raw-secret"},),
        exc_info=None,
    )
    filter_ = SensitiveValueFilter()

    assert filter_.filter(access)
    assert filter_.filter(structured)
    assert "raw-ticket" not in access.getMessage()
    assert "raw-telegram" not in access.getMessage()
    assert "raw-password" not in access.getMessage()
    assert "raw-secret" not in structured.getMessage()


def test_configuration_attaches_redaction_to_uvicorn_access_handler() -> None:
    root = logging.getLogger()
    access_logger = logging.getLogger("uvicorn.access")
    original_root_handlers = list(root.handlers)
    original_root_level = root.level
    original_access_handlers = list(access_logger.handlers)
    access_handler = logging.StreamHandler(io.StringIO())
    access_logger.handlers = [access_handler]
    try:
        configure_logging()
        assert any(
            isinstance(filter_, SensitiveValueFilter)
            for filter_ in access_handler.filters
        )
    finally:
        root.handlers = original_root_handlers
        root.setLevel(original_root_level)
        access_logger.handlers = original_access_handlers
