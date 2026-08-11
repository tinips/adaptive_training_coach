"""Delivery result helpers used by thin Telegram handlers."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup


@dataclass(frozen=True, slots=True)
class TelegramButtonSpec:
    """Serializable button metadata returned by an agent workspace."""

    text: str
    callback_data: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramResponse:
    """One safe user-facing response ready for Telegram delivery."""

    text: str
    keyboard: InlineKeyboardMarkup | None = None
    user_keyboard: ReplyKeyboardMarkup | None = None
    edit_existing: bool = False
    button_rows: tuple[tuple[TelegramButtonSpec, ...], ...] = ()
    clear_agent_thread: bool = False
    refresh_user_keyboard: bool = False
