import ast
import json
import string
import unittest
from pathlib import Path

from common_voice import _normalize, contribution_url
from i18n import DEFAULT_LOCALE, LOCALES_DIR, available_locales, t, ui_locale


def placeholders(value: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(value) if name}


class LocalizationTests(unittest.TestCase):
    def test_ukrainian_is_default_and_unknown_locale_falls_back(self):
        self.assertEqual(DEFAULT_LOCALE, "uk")
        self.assertEqual(ui_locale("de"), "uk")
        self.assertEqual(t("de", "language.name"), t("uk", "language.name"))

    def test_locale_files_are_valid_and_placeholders_match_english(self):
        english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
        self.assertIn("uk", available_locales())
        for path in LOCALES_DIR.glob("*.json"):
            messages = json.loads(path.read_text(encoding="utf-8"))
            for key, value in messages.items():
                self.assertIn(key, english, f"Unknown key {key} in {path.name}")
                self.assertEqual(placeholders(value), placeholders(english[key]), f"Placeholder mismatch for {key} in {path.name}")

    def test_static_translation_keys_exist_in_ukrainian(self):
        ukrainian = json.loads((LOCALES_DIR / "uk.json").read_text(encoding="utf-8"))
        source_dir = Path(__file__).parent
        for path in source_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "t":
                    continue
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                    continue
                key = node.args[1].value
                if isinstance(key, str):
                    self.assertIn(key, ukrainian, f"Missing Ukrainian key {key} used in {path.name}:{node.lineno}")

    def test_common_voice_language_normalization(self):
        language = _normalize({
            "locale": "de",
            "english_name": "German",
            "is_contributable": 1,
            "recordedHours": 12.6,
            "validatedHours": 10.2,
            "speakersCount": 42,
        })
        self.assertEqual(language["recorded"], 13)
        self.assertEqual(language["locale"], "de")
        self.assertIsNone(_normalize({"locale": "xx", "english_name": "Example", "is_contributable": 0}))

    def test_contribution_url_rejects_unsafe_locale(self):
        self.assertEqual(contribution_url("pt-BR"), "https://commonvoice.mozilla.org/pt-BR/listen")
        self.assertEqual(contribution_url("../../bad"), "https://commonvoice.mozilla.org/en/listen")


if __name__ == "__main__":
    unittest.main()
