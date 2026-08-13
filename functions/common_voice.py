"""Common Voice language catalog and contribution URL helpers."""

import re
import time
from urllib.parse import quote

from firebase_admin import db
import requests


LANGUAGES_API = "https://commonvoice.mozilla.org/api/v1/stats/languages"
CACHE_SECONDS = 6 * 60 * 60
LOCALE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
_memory_cache: tuple[float, list[dict]] | None = None


def _normalize(item: dict) -> dict | None:
    locale = str(item.get("locale", ""))
    name = str(item.get("english_name", "")).strip()
    if not LOCALE_PATTERN.fullmatch(locale) or not name or not item.get("is_contributable"):
        return None
    return {
        "locale": locale,
        "english_name": name,
        "recorded": int(round(float(item.get("recordedHours", 0) or 0))),
        "validated": int(round(float(item.get("validatedHours", 0) or 0))),
        "speakers": int(item.get("speakersCount", 0) or 0),
    }


def fetch_languages() -> list[dict]:
    response = requests.get(LANGUAGES_API, timeout=20)
    response.raise_for_status()
    languages = [_normalize(item) for item in response.json() if isinstance(item, dict)]
    return sorted((item for item in languages if item), key=lambda item: item["english_name"].casefold())


def get_languages(force_refresh: bool = False) -> list[dict]:
    global _memory_cache
    now = time.time()
    if not force_refresh and _memory_cache and now - _memory_cache[0] < CACHE_SECONDS:
        return _memory_cache[1]

    stored = db.reference("common_voice_languages").get() or {}
    if not force_refresh and isinstance(stored, dict) and stored:
        languages = sorted(stored.values(), key=lambda item: str(item.get("english_name", "")).casefold())
        _memory_cache = (now, languages)
        return languages

    languages = fetch_languages()
    db.reference("common_voice_languages").set({item["locale"]: item for item in languages})
    _memory_cache = (now, languages)
    return languages


def language_by_locale(locale: str) -> dict | None:
    return next((item for item in get_languages() if item.get("locale") == locale), None)


def contribution_url(locale: str, action: str = "listen") -> str:
    safe_locale = quote(locale if LOCALE_PATTERN.fullmatch(str(locale)) else "en", safe="")
    safe_action = action if action in ("listen", "speak") else "listen"
    return f"https://commonvoice.mozilla.org/{safe_locale}/{safe_action}"
