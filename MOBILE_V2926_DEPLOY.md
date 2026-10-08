# FX AI Mobile v2.9.2.6 — staging branch

This branch stages the **mobile-only** upgrade. Render currently auto-deploys only main, so do not merge until testing and required v2.9.2.6 source files are present.

## What remains to be uploaded
Use the prepared mobile-deployment package from ChatGPT (not the Windows PC installer). The package contains:
- config.json
- forex_app/ (Python trading engine)
- mobile/ (UI and API)
- mobile_cloud/app.py (Flask/Gunicorn compatibility with the existing Render start command)
- mobile_cloud/requirements.txt

IMPORTANT: The repository is public. Review code/privacy before publishing; never commit tokens, certificates, trade databases or account credentials.

## Render configuration already present
Build: pip install -r mobile_cloud/requirements.txt
Start: gunicorn --chdir mobile_cloud app:app --bind 0.0.0.0:$PORT
Use one Gunicorn worker.
The new app requires FX_MOBILE_TOKEN (or existing MOBILE_ACCESS_TOKEN), at least 16 characters. Do not expose its value.

## Test before merge
- GET /health should show v2.9.2.6.
- GET /api/state without Bearer token must return HTTP 401.
- Authenticated GET /api/state should show PAPER-only and correct engine version.
- Test Start, Pause, Stop, chart, Trade Replay, Neural Edge, Shadow Lab, and settings.

## Operational limitation
Free Render can sleep/restart. Ephemeral DB and settings are not durable. This is not a reliable continuous trading setup. TestFlight publication is a separate iOS signing and upload task.
