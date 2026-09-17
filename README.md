# Mod Recon

Mod Recon is an open-source community utility that monitors Arma Reforger servers and reports changes to their mod manifests. The hosted service is free to use and supported by voluntary contributions.

**v0.2 development:** file-based monitoring for multiple servers. Each has its own baseline, confirmation state, history, and Discord destination. Reuse one webhook for a shared channel, or use separate webhooks. The existing NA7 service remains on v0.1.1 until deployment.

## Get started

Requires Python 3.10+. From a downloaded or cloned copy of this repository:

```sh
python -m pip install .
modrecon find "WCS NA7"
modrecon add <server-id> --name "WCS NA7" --webhook-env WCS_WEBHOOK
```

`find` shows matching names, scenarios, player counts, online state, and IDs. Use `--page 2` for more results. `add` verifies the chosen ID and writes `modrecon.yaml`; it never chooses a search result automatically. Omit the optional flags for prompts. Repeat `find` and `add` for each server.

Create `.env` beside the configuration file:

```text
WCS_WEBHOOK=your-discord-webhook-url
```

Keep this file private. Then:

```sh
modrecon check
modrecon run
```

The first valid poll creates a silent baseline. Changes require two consecutive valid observations; offline or incomplete responses never become removal alerts. Stop with Ctrl+C. `modrecon status` reads saved status; `modrecon run --once` polls each server once and processes pending alerts.

## Configuration

```yaml
servers:
  - name: "WCS NA7"
    server_id: "verified-id-from-find"
    webhook_url: "${WCS_WEBHOOK}"
  - name: "WCS NA1"
    server_id: "another-verified-id"
    webhook_url: "${WCS_WEBHOOK}"
poll_interval: 120
confirmation_polls: 2
```

See [modrecon.example.yaml](modrecon.example.yaml). Use `modrecon --config path/to/config.yaml run` for another file. Paths and `.env` resolve beside that configuration. Restart after changing it. Removing an entry stops monitoring it while retaining its history. No automatic server-ID rebinding.

Optional settings: `database_path` (default `data/mod-recon.db`), `donation_url`, `base_url`, `requests_per_minute` (50), and `requests_per_day` (5,000). Limits are conservative local budgets, not additional API entitlement; confirm your upstream allowance before increasing them. Configuration reserves 20% for enrichment/discovery; at defaults, five servers fit a 120-second interval. Increase the interval for larger fleets. The shared request budget and upstream cooldown survive restart.

Workers poll independently. Slow servers and failing destinations do not block unrelated servers; servers sharing a webhook respect its shared cooldown. Each worker handles at most one queued event per cycle. As with v0.1, an ambiguous Discord success response can cause a duplicate; alerts carry an event ID.

## Upgrade from v0.1.1

Stop the old process first, back up its SQLite database, and set `database_path` to that existing file. Keep the same server ID and webhook. v0.2 reuses its baseline, candidates, events, and pending deliveries without rewriting history. Do not run both versions against the same database. A v0.2 process lock prevents two v0.2 runners sharing a database; it cannot detect a legacy v0.1 runner.

Run tests with `python -m unittest -v`. Development installation: `python -m pip install -e .`.

## Support and contribute

**[Support Mod Recon](https://donate.stripe.com/4gM3cv5Eodct0N49VTfw400)** — Keep server updates free, open source, and running for everyone.

Bug reports and pull requests are welcome. No dashboard, bot installation, or accounts are required.

[Changelog](CHANGELOG.md) · [MIT License](LICENSE) · [Data source: ReforgerMods](https://reforgermods.net/arma-reforger-mods-api/v2/)

Independent community project; not affiliated with Bohemia Interactive, ReforgerMods, or WCS.
