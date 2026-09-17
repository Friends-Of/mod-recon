# Mod Recon

Mod Recon is an open-source community utility that monitors Arma Reforger servers and reports changes to their mod manifests. The hosted service is free to use and supported by voluntary contributions.

Currently a small v0.1.1 pilot monitoring **WCS NA7**, with its first real mod deployment successfully detected.

## How it works

Checks every two minutes for added, removed, or updated mods. Changes must appear in two consecutive valid observations before an alert goes to Discord. History is saved locally in SQLite.

Each instance tracks one server. Public signup is not available yet.

## Run it yourself

Requires **Python 3.10+**. No packages to install.

1. Clone or download this repository.
2. Copy `.env.example` to `.env` and set your server ID and Discord webhook URL. Keep the webhook private.
3. Run `python watch.py` from the project folder.

The first check establishes a baseline without sending an alert. Keep the process running to monitor; stop with Ctrl+C. Run only one instance per database.

Run tests with `python -m unittest -v`.

## Support and contribute

**[Support Mod Recon](https://donate.stripe.com/4gM3cv5Eodct0N49VTfw400)** — Keep server updates free, open source, and running for everyone.

Contributions are voluntary. Bug reports and pull requests are welcome.

[Changelog](CHANGELOG.md) · [MIT License](LICENSE) · [Data source: ReforgerMods](https://reforgermods.net/arma-reforger-mods-api/v2/)

Independent community project; not affiliated with Bohemia Interactive, ReforgerMods, or WCS.
