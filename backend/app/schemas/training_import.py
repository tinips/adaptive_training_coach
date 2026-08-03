"""Delivery-neutral metadata for an uploaded Telegram training file."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TelegramDocumentUpload:
    """Safe Telegram metadata; the original filename is display-only."""

    file_id: str
    file_unique_id: str
    display_filename: str
    file_size: int | None
    update_id: int | None
