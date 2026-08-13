# Localization guide

All Telegram menus and user-facing application messages live in JSON files under `functions/locales/`. Python code refers to messages by stable keys and does not contain translated prose.

## Resolution rules

1. The user selects a Common Voice contribution locale.
2. If `functions/locales/{locale}.json` exists, the bot uses it as the UI locale.
3. If the file or an individual key is missing, the bot uses `functions/locales/en.json`.

The contribution language and UI localization are related but distinct. For example, a user can select German for Common Voice; until `de.json` is contributed, links and statistics use German while the bot interface remains English.

## Add a translation

Copy the English catalog using the exact Common Voice locale code:

```bash
cp functions/locales/en.json functions/locales/de.json
```

Translate JSON values only. Keep:

- Every key unchanged.
- Command names such as `/language` unchanged.
- Placeholders such as `{language}`, `{time}`, `{weekday}`, and `{url}` unchanged.
- Valid JSON escaping for newlines and quotation marks.
- Telegram Markdown markers balanced where the English message uses them.

Language names in the selector come from Common Voice's `english_name` field and are not stored in translation catalogs.

## Validate

```bash
cd functions
python -m unittest discover
```

Tests reject unknown translation keys and placeholder mismatches. A translation may temporarily omit keys; those messages safely fall back to English.

## Review checklist

- Ask a fluent speaker to review the translation.
- Test `/start`, `/language`, statistics, contribution, sharing, privacy, and all reminder commands.
- Confirm messages fit comfortably on mobile screens.
- Confirm weekday names are natural inside the translated reminder sentence.
- Do not translate content supplied dynamically by an administrator through `/send_all`.
