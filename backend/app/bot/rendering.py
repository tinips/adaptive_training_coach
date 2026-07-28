"""Delivery result helpers used by thin Telegram handlers."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardMarkup


@dataclass(frozen=True, slots=True)
class TelegramResponse:
    """One safe user-facing response ready for Telegram delivery."""

    text: str
    keyboard: InlineKeyboardMarkup | None = None
    edit_existing: bool = False
