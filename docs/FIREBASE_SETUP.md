# Firebase setup and deployment

This guide describes the cloud resources needed to recreate the bot in a new project.

## 1. Create and select a project

Create a Firebase project in the [Firebase console](https://console.firebase.google.com/) or with the CLI. Upgrade to the Blaze plan before deploying server-side functions.

```bash
firebase login
firebase projects:list
firebase use --add
gcloud config set project YOUR_PROJECT_ID
```

Open-source templates should not commit `.firebaserc`; this repository ignores it deliberately. You can also pass `--project YOUR_PROJECT_ID` to every Firebase command.

## 2. Create Firebase products

In the Firebase console:

1. Create a Realtime Database. Choose a location compatible with the functions' `us-central1` region.
2. Create the default Cloud Storage bucket.
3. Enable billing.

The code reads Firebase's injected `FIREBASE_CONFIG`. For unusual local environments, copy `functions/.env.example` and fill in `DATABASE_URL` and `STORAGE_BUCKET`.

The included rules deny all client access:

```bash
firebase deploy --only database,storage
```

The Admin SDK used inside functions bypasses these client rules.

## 3. Enable Google Cloud services

Firebase deployment normally enables required APIs interactively. They can also be enabled explicitly:

```bash
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtasks.googleapis.com \
  eventarc.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com
```

## 4. Configure Telegram and secrets

Create a bot using BotFather. Copy `functions/.env.example` to `functions/.env` and set the public bot username.

Create secrets:

```bash
firebase functions:secrets:set TOKEN
firebase functions:secrets:set ADMIN_TELEGRAM_ID
firebase functions:secrets:set TELEGRAM_WEBHOOK_SECRET
```

Generate the webhook secret with a cryptographically secure random generator. Telegram user IDs and bot usernames are not the same thing: `ADMIN_TELEGRAM_ID` must be the administrator's numeric user ID.

## 5. Deploy functions

```bash
firebase deploy --only functions
```

The deployment creates:

- One HTTP webhook function.
- One Cloud Tasks queue function and queue.
- One Realtime Database event function.
- Four scheduled functions and their scheduler resources.

Deployment output includes the webhook URL. Save it for Telegram registration.

## Cloud Tasks IAM

Firebase may configure these bindings automatically. If reminder creation reports `PERMISSION_DENIED`, grant the runtime service account permission to create tasks, impersonate itself for OIDC, inspect the task function URI, and invoke the function.

Determine the default gen-2 runtime identity:

```bash
PROJECT_ID="your-project-id"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

Grant project permissions:

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudtasks.enqueuer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudfunctions.viewer"

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountUser"

gcloud functions add-iam-policy-binding deliver_reminder \
  --gen2 \
  --region=us-central1 \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudfunctions.invoker"
```

If you configured a custom service account on the functions, substitute that identity. Apply least privilege and confirm your organization's IAM policy before granting roles.

## 6. Register the webhook

```bash
curl --request POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  --data-urlencode "url=<DEPLOYED_TELEGRAM_BOT_URL>" \
  --data-urlencode "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

The secret must exactly match the Secret Manager value. Requests without Telegram's matching secret header receive HTTP 403.

## 7. Upload optional assets

Upload `progress.png` and `excited.png` to the default bucket root. You may instead adjust the object paths in the Python source.

## 8. Verify

```bash
firebase functions:list
gcloud tasks queues list --location=us-central1
firebase functions:log --only telegram_bot
```

Then test in a private chat:

```text
/start
/remind_daily 14:30
/reminder
/reminder_off
```

Schedule a reminder several minutes ahead and inspect both the `deliver-reminder` queue and function logs.

## Updating

Run tests and redeploy:

```bash
cd functions
python -m unittest test_reminder_schedule.py
cd ..
firebase deploy --only functions
```

After changing a secret value, redeploy every function that binds that secret. Use a separate Firebase project and separate Telegram bot for development.

## Cost and operations

Set a Google Cloud budget alert before deployment. Costs can come from function invocations and compute, Cloud Tasks operations, scheduler jobs, Secret Manager access, database storage/downloads, storage, logs, builds, and artifact images. `min_instances=0` avoids paid idle reminder and webhook instances.

Useful official references:

- [Firebase CLI](https://firebase.google.com/docs/cli)
- [Functions environment and secrets](https://firebase.google.com/docs/functions/config-env)
- [Task queue functions](https://firebase.google.com/docs/functions/task-functions)
- [Realtime Database triggers](https://firebase.google.com/docs/functions/database-events)
