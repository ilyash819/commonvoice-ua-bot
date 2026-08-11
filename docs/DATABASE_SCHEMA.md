# Realtime Database schema

The database is server-managed. Client security rules intentionally deny direct reads and writes.

```text
/
├── users/{telegram_user_id}
│   ├── profile
│   └── reminder
├── latest
├── history/{YYYY-MM-DD}
├── database_image_cache/{YYYY-MM-DD}
├── recurring_broadcasts/{broadcast_id}
└── broadcast_queue/{queue_id}
```

## User

```json
{
  "users": {
    "123456789": {
      "profile": {
        "first_name": "Example",
        "username": "example_user",
        "last_seen": "2026-04-11T14:10:00+00:00"
      },
      "reminder": {
        "enabled": true,
        "frequency": "weekly",
        "weekday": 1,
        "time": "14:30",
        "timezone": "Europe/Kyiv",
        "version": "random-version-id",
        "created_at": "2026-04-11T14:10:00+00:00",
        "next_run_at": "2026-04-17T11:30:00+00:00",
        "last_sent_for": "2026-04-10T11:30:00+00:00"
      }
    }
  }
}
```

`delivery_claim` and `next_scheduled_after` are internal idempotency fields that may appear temporarily. Do not edit them manually. A daily reminder omits `weekday`.

## Statistics

```json
{
  "latest": {
    "recorded": 1200,
    "validated": 900,
    "speakers": 5000,
    "timestamp": "2026-04-11T03:00:00+03:00"
  },
  "history": {
    "2026-08-11": {
      "recorded": 1200,
      "validated": 900,
      "speakers": 5000,
      "timestamp": "2026-04-11T03:00:00+03:00"
    }
  }
}
```

## Annual broadcasts

Add these records through the Firebase console or Admin SDK:

```json
{
  "recurring_broadcasts": {
    "common_voice_day": {
      "month": 2,
      "day": 19,
      "text": "Broadcast text in Telegram Markdown",
      "image_url": "annual/common-voice-day.png",
      "last_run_year": 2025
    }
  }
}
```

`image_url` may be a path in the default bucket, a `gs://` URL, or a Firebase Storage download URL. Omit it for a text-only broadcast. `last_run_year` is updated after delivery and can initially be omitted.

## Broadcast queue

Queue entries are normally created by the administrator command:

```json
{
  "broadcast_queue": {
    "generated_push_id": {
      "text": "Message",
      "image_url": "telegram-file-id",
      "media_source": "telegram_file_id",
      "media_type": "photo",
      "status": "pending",
      "processed": false,
      "created_at": "2026-04-11T14:10:00+00:00"
    }
  }
}
```

The processor adds claim, lease, status, and `delivery_result` fields. Treat queue records as append-only operational data.

## Retention

- A user is deleted after 365 days without `profile.last_seen` activity.
- A user can delete their record immediately with `/delete_me` or `/stop`.
- Generated image caches older than 14 days are deleted.
- Statistics history, recurring broadcasts, and broadcast results have no automatic expiry.
