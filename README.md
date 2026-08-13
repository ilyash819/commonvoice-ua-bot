# Multilingual Common Voice Telegram Bot

A multilingual serverless Telegram bot for contributing to [Mozilla Common Voice](https://commonvoice.mozilla.org/). It displays statistics for each user's chosen language, sends community broadcasts, and supports daily or weekly reminders.

The backend runs on Firebase and Google Cloud. It uses Python Cloud Functions, Realtime Database, Cloud Storage, Secret Manager, Cloud Scheduler, and Cloud Tasks. There is no continuously running server.

## Features

- Dynamic contribution-language catalog from Common Voice.
- Persistent per-user language selection with `/language` switching.
- JSON-based UI translations with English fallback.
- Per-language Common Voice statistics with generated charts.
- Daily collection of all contributable Common Voice language metrics.
- Weekly growth broadcasts.
- Configurable annual/holiday broadcasts.
- Admin photo and video broadcasts with queued delivery.
- Per-user daily and weekly reminders in the `Europe/Kyiv` timezone.
- Event-driven reminders through Cloud Tasks instead of database polling.
- Reminder retry, stale-task invalidation, and duplicate protection.
- Private-chat-only operation and authenticated Telegram webhooks.
- User-controlled deletion through `/delete_me` or `/stop`.
- Automatic deletion of profiles inactive for 365 days.

## Bot commands

| Command | Purpose |
| --- | --- |
| `/start` | Open the main menu and contribution link. |
| `/privacy` | Display the privacy notice. |
| `/language` | Choose a different Common Voice contribution language. |
| `/statistics` | Show current statistics and a progress chart. |
| `/contribute` | Open Common Voice for the selected language. |
| `/remind_daily 14:30` | Create or replace a daily reminder. |
| `/remind_weekly 1 14:30` | Create or replace a weekly reminder; weekdays are 1–7, Monday–Sunday. |
| `/reminder` | Display the current reminder. |
| `/reminder_off` | Disable the current reminder. |
| `/delete_me` or `/stop` | Delete the user's stored profile and reminder. |

The configured administrator can attach a photo or video with a caption beginning with `/send_all`. The remainder of the caption is queued and broadcast to registered users. There is intentionally no endpoint that lists users.

## Architecture

```text
Telegram
   │ authenticated webhook
   ▼
telegram_bot (HTTP function) ──────► Realtime Database
   │                                      │
   │ creates reminder                     │ broadcast_queue create event
   ▼                                      ▼
Cloud Tasks ── scheduled dispatch ─► deliverreminder
                                          │
                                          └──► Telegram

Cloud Scheduler ──► statistics with cleanup, recurring broadcasts
Cloud Storage   ──► optional progress/excitement image assets
```

See [Architecture](docs/ARCHITECTURE.md) and [Database schema](docs/DATABASE_SCHEMA.md) for the detailed execution model.

## Requirements

- Python 3.12.
- Node.js and the [Firebase CLI](https://firebase.google.com/docs/cli).
- A Google account with permission to create a Firebase project.
- A billing-enabled Firebase Blaze project. Cloud Functions, Cloud Scheduler, Cloud Tasks, and Secret Manager require billing, although small installations commonly remain within free allowances.
- A Telegram bot created through [@BotFather](https://t.me/BotFather).
- `gcloud` CLI for the IAM commands used by task queues.

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY

python3.12 -m venv functions/venv
source functions/venv/bin/activate
python -m pip install -r functions/requirements.txt

npm install --global firebase-tools
firebase login
gcloud auth login
```

### 2. Create Firebase resources

Create or select a Firebase project, upgrade it to Blaze, and create:

- A Realtime Database in a region compatible with `us-central1` functions.
- A default Cloud Storage bucket.
- A Telegram bot and its token.

Then associate this folder with your project:

```bash
firebase use --add
```

For a public fork, `.firebaserc` remains ignored so your personal project ID is not committed. An example is provided in [.firebaserc.example](.firebaserc.example).

### 3. Configure non-secret values

```bash
cp functions/.env.example functions/.env
```

Set `BOT_USERNAME` without the leading `@`. Firebase normally injects the database URL and storage bucket automatically. Keep `DATABASE_URL` and `STORAGE_BUCKET` in `.env` only if automatic discovery does not work in your environment.

### 4. Store secrets

Create a random webhook secret, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store the three values in Google Secret Manager through Firebase:

```bash
firebase functions:secrets:set TOKEN
firebase functions:secrets:set ADMIN_TELEGRAM_ID
firebase functions:secrets:set TELEGRAM_WEBHOOK_SECRET
```

- `TOKEN`: token supplied by BotFather.
- `ADMIN_TELEGRAM_ID`: numeric Telegram user ID allowed to broadcast.
- `TELEGRAM_WEBHOOK_SECRET`: random value Telegram sends with every webhook request.

Never commit tokens, `.secret.local`, service-account JSON files, or populated `.env` files.

### 5. Deploy

```bash
firebase deploy --only functions,database,storage
```

The first deployment of `deliverreminder` creates its Cloud Tasks queue. If task creation or invocation reports a permission error, complete the IAM procedure in [Firebase setup](docs/FIREBASE_SETUP.md#cloud-tasks-iam).

### 6. Register the Telegram webhook

Find the deployed URL for `telegram_bot` in the deployment output or Firebase console. Then call Telegram's `setWebhook` API:

```bash
curl --request POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  --data-urlencode "url=<TELEGRAM_BOT_FUNCTION_URL>" \
  --data-urlencode "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Do not paste a real token into shared logs or screenshots. Verify registration with:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

Open the bot in a private Telegram chat and send `/start`.

## Localization

English is the required fallback locale. Ukrainian is included as an example translation. To add another UI language:

1. Copy `functions/locales/en.json` to `functions/locales/LOCALE.json`, where `LOCALE` matches the Common Voice locale code.
2. Translate values only; do not rename keys or placeholders such as `{language}` and `{time}`.
3. Run `cd functions && python -m unittest discover`.
4. Open a pull request and mention the language and reviewer.

A user's chosen contribution locale selects the same UI locale when that JSON file exists. Otherwise every UI string falls back to English. Language names in the selector use the English names supplied by Common Voice.

## Optional image assets

Upload these files to the root of the default Firebase Storage bucket:

| File | Used for | Required |
| --- | --- | --- |
| `progress.png` | Background for generated statistics charts. | No; a white background is used when absent. |
| `excited.png` | Weekly growth broadcast banner. | Yes for the weekly growth message; that broadcast is skipped when absent. |

The included Storage rules deny client access. Cloud Functions use the Admin SDK and can still read the files.

## Local development

```bash
cp functions/.secret.local.example functions/.secret.local
cp functions/.env.example functions/.env.local
firebase emulators:start --only functions,database,storage
```

Local task queue testing has limitations and may use deployed Google Cloud services depending on credentials. Use a separate development Firebase project and never point emulator secrets at a production bot.

Run the tests:

```bash
cd functions
python -m unittest discover
```

## Deployment notes

- Functions default to `us-central1`; keep the Realtime Database close to that region or update all function regions consistently.
- Reminders are stored in local Kyiv time but queued using UTC instants, preserving daylight-saving behavior.
- Cloud Tasks can schedule only a limited distance into the future. Daily and weekly reminders schedule one occurrence at a time, so they remain inside that limit.
- Previously queued tasks are not deleted when a reminder changes. Their version no longer matches the database record, so they exit without sending.
- Cloud Tasks provide at-least-once delivery. The database claim and deterministic task ID reduce duplicates, but no external Telegram delivery can provide an absolute exactly-once guarantee.

## Documentation

- [Firebase setup and deployment](docs/FIREBASE_SETUP.md)
- [Architecture and execution flows](docs/ARCHITECTURE.md)
- [Realtime Database schema](docs/DATABASE_SCHEMA.md)
- [Localization guide](docs/LOCALIZATION.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## License

This project is available under the [MIT License](LICENSE). Mozilla and Common Voice are trademarks of the Mozilla Foundation. This repository is an independent community project and is not an official Mozilla product.
