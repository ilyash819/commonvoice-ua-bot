# Architecture

## Components

| Component | Trigger | Responsibility |
| --- | --- | --- |
| `telegram_bot` | HTTPS webhook | Authenticate Telegram, update profiles, handle commands, and enqueue admin broadcasts or reminders. |
| `deliverreminder` | Cloud Tasks | Claim a due reminder, send it, and enqueue the next occurrence. |
| `process_broadcast_queue_item` | Realtime Database create | Atomically claim and deliver an administrator broadcast. |
| `collect_voice_stats` | Daily schedule | Refresh Ukrainian metrics, remove inactive profiles, and delete old graph caches. |
| `weekly_sunday_growth_broadcast` | Weekly schedule | Send Ukrainian Common Voice progress to users. |
| `daily_recurring_broadcast_checker` | Daily schedule | Find matching annual broadcasts and deliver at most once per year. |

`functions/main.py` is the deployment entry point. Implementations are separated into webhook, scheduler, reminder, and shared modules.

## Telegram request flow

```text
Telegram POST
   │
   ├─ wrong method or secret ─► 403/405
   │
   ▼
Deserialize update
   │
   ├─ not a private text/media message ─► ignore
   │
   ▼
Update users/{telegram_id}/profile
   │
   ├─ no language ─► paginated Common Voice language selector
   │                         │
   │                  store preferences/language
   │                         │
   ▼
Execute command and return HTTP 200
```

Telegram supplies `X-Telegram-Bot-Api-Secret-Token`. The webhook compares it with `TELEGRAM_WEBHOOK_SECRET` using a constant-time comparison.

## Reminder flow

```text
/remind_daily 14:30
   │
   ├─ validate HH:MM
   ├─ generate reminder version
   ├─ calculate next Europe/Kyiv occurrence
   ├─ store users/{uid}/reminder
   └─ enqueue deterministic Cloud Task
                         │
                  waits until UTC time
                         │
                         ▼
                  deliverreminder
                         │
              ┌──────────┴──────────┐
              │                     │
       version matches       missing/old version
              │                     │
       transaction claim            └─► exit
              │
       Telegram delivery
              │
       record last_sent_for
              │
       enqueue next occurrence
```

Every edit creates a new random version. Old queued tasks are harmless because their payload version no longer matches. Task IDs are hashes of user, version, and occurrence; retrying an enqueue therefore cannot create a second task for the same occurrence.

## Localization flow

The selected contribution locale is stored separately from the resolved UI locale. `functions/locales/en.json` is always the fallback. If `functions/locales/{contribution_locale}.json` exists, the bot loads it; missing keys still resolve from English. Menus are constructed per request, so changing language takes effect immediately.

## Broadcast flow

The administrator sends a photo or video whose caption starts with `/send_all`. The webhook stores a pending item under `broadcast_queue`. A database-create function transactionally claims it, sends it with bounded concurrency and Telegram-aware pacing, and records the delivery result.

The queue is retained as an audit record. Failed broadcasts are marked `failed`; they are not automatically retried because partial broadcast retries could duplicate messages for successful recipients.

## Statistics and images

`collect_voice_stats` writes `latest/{locale}` and `history/{locale}/YYYY-MM-DD`. When a user requests statistics, Matplotlib renders that language's series and caches its PNG under `database_image_cache/{locale}/YYYY-MM-DD`.

## Trust boundaries

- Realtime Database and Storage client rules deny all access.
- Cloud Functions access Firebase through the Admin SDK.
- Telegram is allowed to call only the webhook URL, protected by its secret header.
- Cloud Tasks invoke a private task function using a service-account identity.
- Secrets are bound only to functions that declare them.
- Administrator authorization uses an exact numeric Telegram user ID.
