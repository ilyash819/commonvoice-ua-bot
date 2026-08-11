# Security policy

## Reporting a vulnerability

Do not disclose vulnerabilities or exposed credentials in a public issue. Use GitHub's private vulnerability reporting feature for this repository. If it is unavailable, contact the repository owner privately.

Include affected components, reproduction steps, likely impact, and any suggested mitigation. Do not access data belonging to other users while investigating.

## Deployment security checklist

- Store Telegram tokens and webhook secrets in Secret Manager.
- Never commit `.env`, `.secret.local`, `.firebaserc`, database exports, or service-account keys.
- Keep Realtime Database and Storage rules deny-by-default unless a client application is intentionally added.
- Use a long random Telegram webhook secret and rotate it after suspected exposure.
- Restrict `ADMIN_TELEGRAM_ID` to one trusted numeric user ID.
- Grant Cloud Tasks permissions only to the functions' runtime service account.
- Enable billing alerts, log monitoring, and Google account multi-factor authentication.
- Use separate Firebase projects and Telegram bots for development and production.
- Review retained Telegram profile data and comply with applicable privacy law.

## Supported versions

Security fixes are applied to the latest version on the default branch. Older deployments should update and redeploy.
