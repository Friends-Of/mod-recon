# Operations

This is the operator reference for a self-hosted Mod Recon v0.2 instance.

## Configuration and secrets

Install with Python 3.10+ and keep `modrecon.yaml` and `.env` beside the installation or pass `--config`. `.env` is read as simple `KEY=VALUE` lines; existing process environment values win. Use `${ENVIRONMENT_VARIABLE}` in YAML so webhook URLs are never written to configuration or Git. Treat webhook URLs as passwords and restrict access to `.env`, YAML, SQLite files, and logs.

The YAML file is limited to 256 KiB and does not allow anchors or aliases. Webhooks must be HTTPS Discord webhook URLs. `database_path` defaults to `data/mod-recon.db` relative to the configuration. The donation URL is optional and must be HTTPS.

## Polling budgets and limits

The default poll interval is 120 seconds and confirmation count is 2. Conservative defaults allow 50 upstream requests per minute and 5,000 per day; configuration reserves 20% of the daily and minute budgets for enrichment and discovery. A shared SQLite budget and upstream cooldown survive restarts. Increase limits only when the upstream allowance is verified.

Upstream response bodies are capped at 4,000,000 bytes. Individual metadata fields over 64 KiB are marked unavailable. Discord display fields are capped at 8 KiB and text attachments at 512 KiB; shortened output says so, while stored manifests and history remain intact.

## Workers and restart behavior

Each configured server has an independent worker and SQLite state. A slow or failing server does not stop unrelated workers. Workers are staggered during startup; each cycle polls and processes at most one pending event. Posts sharing a webhook use a shared one-second success cooldown and respect retry delays.

The v0.2 process lock prevents two v0.2 runners from sharing one database. It does not detect a legacy v0.1.1 process. Do not run incompatible versions against the same database. Restarting resumes accepted state, candidates, history, and pending events.

An ambiguous Discord acknowledgement can result in a duplicate after retry. Every event has a stable event ID so duplicates can be identified.

## History, upgrades, and backups

History is stored in the configured SQLite database (default `data/mod-recon.db`); the shared budget is stored beside it as `.api.db`. Removing a server from YAML stops monitoring it but retains its history. There is no automatic server-ID rebinding.

Before upgrading from v0.1.1, stop the old process and make a labeled SQLite backup. Point v0.2 at the existing database to reuse its baseline, candidates, events, and pending deliveries. Never run both versions against that file.

## Useful commands

```sh
modrecon check
modrecon status
modrecon run --once
modrecon run
```

`check` validates without polling or posting. `status` reads saved state without polling. `run --once` performs one poll and delivery cycle per configured server. For a background Windows service, `run_service.py` writes rotating logs to `data/service.log`.

If a server does not appear in `find`, try a shorter query or another `--page`. If a webhook fails, verify its environment variable, run `check`, and inspect the queued event status without exposing the URL. If an event remains pending, preserve the database and logs for diagnosis.

See [Architecture](ARCHITECTURE.md) for state transitions and authority rules.
