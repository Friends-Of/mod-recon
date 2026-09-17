# Reforger Watch

Reforger Watch is an open-source community utility that monitors Arma Reforger servers and reports changes to their mod manifests. The hosted service is free to use and supported by voluntary contributions.

## What it does

- Polls the server-reported mod list through the ReforgerMods V2 API every 120 seconds.
- Detects added mods, removed mods, and exact version changes.
- Requires two consecutive safe observations before confirming a change.
- Stores manifests and change history in SQLite.
- Enriches changed versions with available Workshop metadata and posts to Discord.
- Summarizes large deployments and attaches the full change list.

Offline, stale, and incomplete responses are rejected. Name and order changes do not trigger alerts. Release contents are not inferred from version numbers.

## Status

The initial deployment monitors **WCS NA7** and has recorded its first confirmed deployment. Each instance currently tracks one configured server. The hosted service is an early community pilot; public self-service signup and multi-server management are not available yet.

This source includes compact notifications prepared for the next service update. The running hosted collector has intentionally not been restarted. See [CHANGELOG.md](CHANGELOG.md) for running and pending changes.

## Run it yourself

Requires **Python 3.10+**, with no third-party packages.

```sh
git clone https://github.com/Friends-Of/reforger-watch.git
cd reforger-watch
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell or `cp .env.example .env` on Linux/macOS). Configure your server ID and Discord webhook, then run:

```sh
python watch.py --once
python watch.py --status
python watch.py
```

The first valid observation creates a baseline without notifying. Without a webhook, events queue locally. `--once` performs one poll and processes pending deliveries. Stop with Ctrl+C.

Run only one monitor per database. Restarting preserves the baseline and history. Use an absolute database path when launching from another directory. Monitoring requires an awake, network-connected machine. The optional `run_service.py` entrypoint writes rotating logs to `data/service.log`; it does not install a scheduled task automatically.

## Configuration

| Variable | Purpose |
| --- | --- |
| `REFORGER_SERVER_ID` | Verified upstream ID; the example is WCS NA7. |
| `SERVER_LABEL` | Display name for notifications. |
| `DISCORD_WEBHOOK_URL` | Secret webhook destination; leave empty to queue locally. |
| `DATABASE_PATH` | SQLite file; default `./data/reforger-watch.db`. |
| `POLL_INTERVAL_SECONDS` | Poll interval; default/minimum 120 seconds. |
| `REFORGERMODS_BASE_URL` | Default `https://api.reforgermods.net/v2`. |
| `CLIENT_NAME` | API client identifier; default `reforger-watch/0.1`. |
| `DONATION_URL` | Optional public **project donation link** beneath notifications. |

Environment variables override `.env`. Never commit credentials, webhook URLs, databases, or logs. Upstream server ID changes require manual verification; automatic rebinding is not supported.

## Notifications

Updates with more than 12 changes receive compact summaries. A text attachment preserves every change, ID, before/after version, and available metadata. Package totals describe **changed package size**, not guaranteed client download requirements. Metadata failures do not prevent notification.

Confirmed events persist before delivery; failed deliveries are retried. Discord provides no webhook idempotency key, so a lost success response or interruption before recording success can cause a duplicate. Alerts include a stable event ID.

## Tests and preview

```sh
python -m unittest -v
python preview_notification.py
```

Tests do not post to Discord. Preview requires an existing recorded event and reads the database without changing it, polling upstream, or sending a message. Generated files go to `preview/`.

## Support the project

**Support Reforger Watch** — Keep server updates free, open source, and running for everyone.

Contributions are voluntary. The project donation link will be added when available. Bug reports and improvements are welcome through Issues and pull requests. Remove credentials and private configuration from reports.

## Data source and license

Data comes from the independent [ReforgerMods V2 API](https://reforgermods.net/arma-reforger-mods-api/v2/). Upstream availability, freshness, and compatibility are outside this project's control. Reforger Watch is not affiliated with Bohemia Interactive, ReforgerMods, or WCS.

Source code is available under the [MIT License](LICENSE). Third-party Workshop content and trademarks remain the property of their respective owners.
