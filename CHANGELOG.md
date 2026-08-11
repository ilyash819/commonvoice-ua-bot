# Changelog

All notable changes to this project are documented here.

## Unreleased

### Added

- Authenticated Telegram webhook handling.
- Common Voice statistics collection, charts, and daily image caching.
- Queued administrator photo and video broadcasts.
- Annual recurring broadcasts and weekly growth announcements.
- Per-user daily and weekly Cloud Tasks reminders.
- Privacy notice, immediate user deletion, and retention cleanup.
- Reproducible Firebase setup, architecture, schema, security, and contribution documentation.

### Changed

- Reminder delivery is event-driven and no longer scans the user database.
- Firebase project and storage identifiers are discovered from runtime configuration.

### Removed

- The `/admin_users` command and user-list report.
