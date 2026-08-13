# Contributing

Contributions are welcome through issues and pull requests.

## Development setup

```bash
python3.12 -m venv functions/venv
source functions/venv/bin/activate
python -m pip install -r functions/requirements.txt
cd functions
python -m unittest discover
```

Use a dedicated development Firebase project and Telegram bot. Never test broadcasts against production user data.

## Guidelines

- Keep `functions/main.py` limited to exported Firebase entry points.
- Put pure scheduling logic in `reminder_schedule.py` so it remains easy to test.
- Use Realtime Database transactions for claims and other concurrency-sensitive updates.
- Assume Cloud Tasks and database events can be delivered more than once.
- Preserve user deletion and privacy behavior.
- Add tests for scheduling, timezone, retry, or idempotency changes.
- Keep every user-facing string in `functions/locales/*.json`; English keys are the source of truth.
- Preserve translation placeholders exactly and name locale files using Common Voice locale codes.
- Do not commit secrets, Firebase project aliases, production database exports, or service-account credentials.

## Pull requests

Explain the user-visible behavior, operational impact, tests performed, and any deployment or IAM changes. Keep unrelated refactoring out of focused fixes.
