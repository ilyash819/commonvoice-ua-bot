"""JSON-backed Ukrainian user interface messages."""

from functools import lru_cache
import json
from pathlib import Path

import telegram


DEFAULT_LOCALE = "uk"
LOCALES_DIR = Path(__file__).with_name("locales")


@lru_cache(maxsize=None)
def _messages(locale: str) -> dict:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def available_locales() -> set[str]:
    return {path.stem for path in LOCALES_DIR.glob("*.json")}


def ui_locale(contribution_locale: str | None) -> str:
    return contribution_locale if contribution_locale in available_locales() else DEFAULT_LOCALE


def t(locale: str, key: str, **values) -> str:
    template = _messages(locale).get(key) or _messages(DEFAULT_LOCALE).get(key) or key
    return template.format(**values)


def main_menu(locale: str) -> telegram.ReplyKeyboardMarkup:
    return telegram.ReplyKeyboardMarkup(
        [
            [telegram.KeyboardButton(text=t(locale, "menu.contribute"))],
            [telegram.KeyboardButton(text=t(locale, "menu.share"))],
            [telegram.KeyboardButton(text=t(locale, "menu.statistics"))],
            [telegram.KeyboardButton(text=t(locale, "menu.reminders"))],
            [telegram.KeyboardButton(text=t(locale, "menu.privacy"))],
        ],
        resize_keyboard=True,
    )
