# Forex Trading V1 v2.9.2.6 — Mobile-only Render deployment

This is a **separate mobile-server deployment** of the v2.9.2.6 PAPER engine.
It does not update or launch the Windows desktop application.

## Use with the existing Render service

- Service: `forex-trading-v1-mobile` on the confirmed `forex trading` workspace.
- Build command (already configured): `pip install -r mobile_cloud/requirements.txt`
- Start command (already configured): `gunicorn --chdir mobile_cloud app:app --bind 0.0.0.0:$PORT`
- Branch (already configured): `main`; staging can be tested separately first.
- Set `FX_MOBILE_TOKEN` to a **random secret of at least 16 characters** in Render if the earlier `MOBILE_ACCESS_TOKEN` is absent. Never commit the token to GitHub.
- The mobile API supports Bearer-token authorization, is PAPER-only and starts paused.
- Use **one Gunicorn worker**: two workers would each run a separate trading engine.

## Required source files in the GitHub deployment

Copy these from this package into the deployment repository, preserving paths:

- `config.json`
- `forex_app/` (all Python source modules)
- `mobile/` (server, web and its assets)
- `mobile_cloud/app.py`
- `mobile_cloud/requirements.txt`

Do NOT upload Windows executables, databases, credentials, private keys, Apple certificates, or local PAPER trade logs. Review repository privacy before uploading strategy source. The existing GitHub repository is public.

## Limitations that matter

Render **Free** can sleep after inactivity, restart, and lose `/tmp` files. PAPER trade history and positions on this free instance are **not durable**. No live broker orders are placed. This is unsuitable for unattended reliable 24/7 trading. For persistence use a durable database and an always-running paid server.

This server makes the updated UI available through mobile Safari/Chrome; TestFlight still requires a separate iOS wrapper build, signing and upload to App Store Connect.

## Verification

- `GET /health` -> `version: 2.9.2.6`
- `GET /api/state` without token -> `401`
- `GET /api/state` with token -> `version: 2.9.2.6`, `paper_only: true`, `running: false` on first launch
- Manually verify Charts, Replay, Shadow Lab, Settings, paper-only Start/Pause/Stop.
