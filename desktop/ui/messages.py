"""Russian copy for GUI toasts (no raw English exception leftovers)."""

from __future__ import annotations

import re

from desktop.lifecycle.actions import Action

_PHRASES = (
    ("Password is required", "Нужен пароль"),
    ("passwords do not match", "пароли не совпадают"),
    ("not found", "не найден"),
    ("timed out", "истекло время ожидания"),
    ("timeout", "таймаут"),
    ("failed", "не удалось"),
    ("permission denied", "нет прав"),
    ("access denied", "нет доступа"),
)


def format_user_error(err: object) -> str:
    text = str(err or "").strip()
    if not text:
        return "Не удалось выполнить операцию"
    for action in Action:
        text = re.sub(rf"\b{re.escape(action.value)}\b", action.ru_name, text, flags=re.I)
    for eng, rus in _PHRASES:
        text = re.sub(re.escape(eng), rus, text, flags=re.I)
    return text
