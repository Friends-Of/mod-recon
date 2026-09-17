# Working on Mod Recon

## Project purpose

Mod Recon monitors the server-reported mod manifests of Arma Reforger servers and records and reports confirmed changes.

## Current milestone

The current milestone is v0.2: configurable multi-server monitoring. It supports independent server state and history, discovery CLI, Discord destinations, confirmation behavior, metadata enrichment, and restart-safe operation. Do not implement roadmap features unless the active request explicitly asks for them.

## Critical invariants

The server-reported manifest is authoritative for what a monitored server is using. Never infer that a server adopted a Workshop update because a newer Workshop version exists. Workshop metadata is enrichment, not authoritative server state.

Manifest changes require the configured number of consecutive identical, valid observations before acceptance. Offline, stale, malformed, incomplete, and failed upstream responses must never become removal events. Preserve these semantics.

Before modifying persistence, polling, confirmation, event generation, delivery, or configuration behavior, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/OPERATIONS.md](docs/OPERATIONS.md), and the existing tests.

## Scope discipline

Do not opportunistically build a web dashboard, hosted account system, Discord installation bot, AI summaries, mod explanations, sponsorship or advertising, public website, or unrelated analytics. Those require an explicit milestone request.

## Development expectations

- Support Python 3.10+; Python 3.14.7 is the current tested runtime.
- Install with `python -m pip install -e .` and run `python -m unittest discover -q`.
- Keep webhook URLs in environment variables or `.env`; never commit secrets, databases, logs, or local operations files.
- Preserve existing SQLite history and use backups before migrations or upgrades.
- Update the relevant documentation when behavior changes; keep README user-facing and put implementation detail in `docs/`.

## Testing expectations

Preserve the existing tests. Add or update tests when behavior changes. Do not weaken safety or validation tests to make an implementation pass.
