# Operations

This is the operator reference for self-hosted Mod Recon Core, including v0.2.9 hardening.

## Configuration and secrets

Install with Python 3.10+ and keep `modrecon.yaml` and `.env` beside the installation or pass `--config`. `.env` is read as simple `KEY=VALUE` lines; existing process environment values win. Use `${ENVIRONMENT_VARIABLE}` in YAML so webhook URLs are never written to configuration or Git. Treat webhook URLs as passwords and restrict access to `.env`, YAML, SQLite files, and logs.

The YAML file is limited to 256 KiB and does not allow anchors or aliases. Webhooks must be HTTPS Discord webhook URLs. `database_path` defaults to `data/mod-recon.db` relative to the configuration. The donation URL is optional and must be HTTPS.

## Polling budgets and limits

The default poll interval is 120 seconds and confirmation count is 2. Conservative defaults allow 50 upstream requests per minute and 5,000 per day; configuration reserves 20% of the daily and minute budgets for enrichment and discovery. A shared SQLite budget and upstream cooldown survive restarts. Increase limits only when the upstream allowance is verified.

In v0.2.9, optional requests are also capped at that 20% at runtime. At free defaults this is 10/minute and 1,000/day for enrichment, discovery, and quota checks combined. Five servers at 120 seconds require 3,600 base polls/day, leaving 400/day beyond that optional allowance for polling retries and operational headroom. Development clients must use the same budget database to share local accounting; other applications can still consume the upstream IP quota. When enrichment is unavailable or its allowance is exhausted, current events report unavailable metadata and delivery can proceed.

Core needs no paid API key. To opt in, set `REFORGERMODS_API_KEY` in the private `.env` beside the configuration. `api_key_env` may select another environment-variable name; never put a literal key in YAML. Credentials are accepted only for the official HTTPS V2 endpoint, authenticated redirects are blocked, and authentication failures never fall back to anonymous requests. `modrecon quota` checks the live effective allowance. `check` remains offline. Authenticated runners verify quota before creating workers. Optional settings `required_api_plan: developer` (or `pro`) and `minimum_api_daily_quota: 100000` enforce a deployment requirement; mismatch aborts startup, without changing the interval. These are not required for ordinary free self-hosting.

The shared cache is stored at `<database_path>.metadata.db`, alongside the budget at `<database_path>.api.db`. Metadata cache entries expire after 24 hours (failures after 60 seconds), independently of manifest state. No cache warm-up runs for silent baselines. Cache data may be rebuilt; preserve the main history database and the API budget through upgrades. Keep those local files private and out of Git.

Upstream response bodies are capped at 4,000,000 bytes. Individual metadata fields over 64 KiB are marked unavailable. Discord display fields are capped at 8 KiB and text attachments at 512 KiB; shortened output says so, while stored manifests and history remain intact.

## Workers and restart behavior

Each configured server has an independent worker and SQLite state. A slow or failing server does not stop unrelated workers. Workers are staggered during startup; each cycle polls and processes at most one pending event. Posts sharing a webhook use a shared one-second success cooldown and respect retry delays.

The v0.2 process lock prevents two v0.2 runners from sharing one database. It does not detect a legacy v0.1.1 process. Do not run incompatible versions against the same database. Restarting resumes accepted state, candidates, history, and pending events.

An ambiguous Discord acknowledgement can result in a duplicate after retry. Every event has a stable event ID so duplicates can be identified.

## History, upgrades, and backups

History is stored in the configured SQLite database (default `data/mod-recon.db`); the shared budget is stored beside it as `.api.db`. Removing a server from YAML stops monitoring it but retains its history. There is no automatic server-ID rebinding.

Before upgrading from v0.1.1, stop the old process and make a labeled SQLite backup. Point v0.2 at the existing database to reuse its baseline, candidates, events, and pending deliveries. Never run both versions against that file.

## Useful commands

### Verified backup before an upgrade

Stop the runner and other clients using the same history/budget files first. Then run `modrecon backup --output data/backups/before-upgrade` with a new private destination directory. The command refuses an active v0.2 runner, missing history, or an existing destination. It uses SQLite's backup API, verifies integrity and foreign-key references, and writes `complete.json` with SHA-256 checksums only after all included files pass. It copies history and any existing API-budget database; the rebuildable metadata cache is omitted. It makes no API requests and sends no notifications.

Check the command exit code before proceeding. A directory without `complete.json` is an incomplete backup, even if individual files exist. Keep it for diagnosis and choose a new directory for a retry. Protect backups like the original history database. Save private configuration and service definitions separately; the command does not copy secrets or service settings. A legacy v0.1 runner does not hold the v0.2 lock, so it must be stopped explicitly.

In PowerShell, `$ErrorActionPreference` alone does not reliably stop execution after a failed native executable. Use an explicit gate:

```powershell
modrecon backup --output data/backups/before-upgrade
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; deployment stopped.' }
modrecon check
if ($LASTEXITCODE -ne 0) { throw 'Configuration failed; deployment stopped.' }
# Only now perform the planned upgrade/validation/start steps.
```

To restore, stop all clients, preserve the current files separately, and restore `history.db` to the configured database path and `api-budget.db` to that path plus `.api.db`. Use a matching code version. Verify the recorded checksums, database integrity, and configuration before starting; restore the budget to retain quota accounting. The v0.2.9 backup command changes no database schema.

### Monitoring

```sh
modrecon check
modrecon quota
modrecon status
modrecon run --once
modrecon run
```

`check` validates without polling or posting. `status` reads saved state without polling. `run --once` performs one poll and delivery cycle per configured server. For a background Windows service, `run_service.py` writes rotating logs to `data/service.log`.

In v0.2.9, `run --once` also staggers worker starts according to the configured minute allowance. Persistent workers retain interval-based staggering. Once-mode can deliver real pending or confirmed events; validate against a copied database and mocked destinations when testing silent initialization. Quota checks consume an optional upstream request.

If a server does not appear in `find`, try a shorter query or another `--page`. If a webhook fails, verify its environment variable, run `check`, and inspect the queued event status without exposing the URL. If an event remains pending, preserve the database and logs for diagnosis.

See [Architecture](ARCHITECTURE.md) for state transitions and authority rules.
