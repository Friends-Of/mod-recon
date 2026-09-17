# Mod Recon

**Know what changed.**

Mod Recon is an open-source community utility that monitors Arma Reforger servers and reports when their mod manifests change—what was added, removed, or updated.

Free · Open source · Community supported

**Current release: v0.2**

## What it does

Mod Recon checks public servers through the ReforgerMods API, confirms a change across consecutive observations, and posts a readable update to Discord. Large deployments are summarized, with the complete recorded change list attached as a text file.

Example:

```text
Example Conflict #1 Mod Update
46 server mods changed
+3 Added · ↑42 Updated · −1 Removed

Major update: most packages moved to 8.2.0
12.34 GiB across 41 changed packages

Full list of all 46 changes: attached text file.
Support Mod Recon — Keep server updates free, open source, and running for everyone.
```

Mod Recon validates upstream data, requires confirmed observations before reporting changes, and includes safeguards for incomplete manifests, rate limits, oversized responses, and Discord delivery failures.

## Quick start

Use Python 3.10 or newer (Python 3.14.7 is the current tested runtime):

```sh
python -m pip install .
modrecon find "Everon Alpha"
modrecon add <server-id> --name "Everon Alpha" --webhook-env EXAMPLE_WEBHOOK
```

Create `.env` beside the configuration file and keep it private:

```text
EXAMPLE_WEBHOOK=your-discord-webhook-url
```

Then validate and run:

```sh
modrecon check
modrecon run
```

The first valid poll creates a silent baseline. Use `modrecon status` to inspect saved state or `modrecon run --once` for one poll cycle. See [Operations](docs/OPERATIONS.md) for installation, backups, troubleshooting, and limits.

## Monitor multiple servers

```yaml
servers:
  - name: "Example Conflict #1"
    server_id: "verified-id-from-find"
    webhook_url: "${EXAMPLE_WEBHOOK}"
  - name: "Everon Alpha"
    server_id: "another-verified-id"
    webhook_url: "${EXAMPLE_WEBHOOK}"
poll_interval: 120
confirmation_polls: 2
```

Each server has independent state, history, change events, and destination. Servers may share a webhook. See [modrecon.example.yaml](modrecon.example.yaml).

## Learn more

- [Architecture](docs/ARCHITECTURE.md) — how polling, confirmation, diffs, enrichment, and delivery work
- [Operations](docs/OPERATIONS.md) — reliable self-hosting and troubleshooting
- [Roadmap](docs/ROADMAP.md) — current milestone and possible future directions
- [Changelog](CHANGELOG.md)
- [AGENTS.md](AGENTS.md) — contributor and coding-agent guidance

## Support and contribute

**[Support Mod Recon](https://donate.stripe.com/4gM3cv5Eodct0N49VTfw400)** — Keep server updates free, open source, and running for everyone.

Bug reports and pull requests are welcome. Mod Recon v0.2 is self-hosted and configured locally.

Licensed under [MIT](LICENSE). Data comes from the [ReforgerMods API](https://reforgermods.net/arma-reforger-mods-api/v2/). Mod Recon is an independent community project and is not affiliated with Bohemia Interactive or ReforgerMods.
