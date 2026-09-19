# Working on Mod Recon

## Project purpose

Mod Recon monitors the server-reported mod manifests of Arma Reforger servers and records and reports confirmed changes.

## Current milestone

v0.2.9 is the production-hardening baseline. v0.3 implements normalized ChangeEvents independently of delivery and is undergoing release review. Do not implement other roadmap features unless the active request explicitly asks for them. Migration of a production database requires the user's explicit upgrade approval.

Core must remain usable on anonymous/free access by default; upstream API credentials are optional. Production changes require the user's explicit deployment scope. Preserve existing database identity, history, and configuration during development.

## Critical invariants

The server-reported manifest is authoritative for what a monitored server is using. Never infer that a server adopted a Workshop update because a newer Workshop version exists. Workshop metadata is enrichment, not authoritative server state.

Manifest changes require the configured number of consecutive identical, valid observations before acceptance. Offline, stale, malformed, incomplete, and failed upstream responses must never become removal events. Preserve these semantics.

Before modifying persistence, polling, confirmation, event generation, delivery, or configuration behavior, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/OPERATIONS.md](docs/OPERATIONS.md), and the existing tests.

## Scope discipline

Core owns observation, validation, confirmation, accepted state, diffing, exact mod/version identity, reliable metadata enrichment, history, structured events, webhook delivery, CLI/self-host configuration, API budgeting, persistence, and integration interfaces.

Guild accounts, commercial subscriptions, paid plans, billing, entitlements, premium gating, hosted administration, dashboards, advertisements, public discovery products, AI summaries, and companion apps are outside this repository's scope. Keep separate product plans outside the public repository. Do not restrict useful Core capabilities for self-hosters.

Version strings are opaque. Grouping requires explicit evidence; package-size totals are not actual download sizes. Preserve the existing license.

## Development expectations

- Support Python 3.10+; Python 3.14.7 is the current tested runtime.
- Install with `python -m pip install -e .` and run `python -m unittest discover -q`.
- Keep webhook URLs in environment variables or `.env`; never commit secrets, databases, logs, or local operations files.
- Preserve existing SQLite history and use backups before migrations or upgrades.
- Update the relevant documentation when behavior changes; keep README user-facing and put implementation detail in `docs/`.

## Testing expectations

Preserve the existing tests. Add or update tests when behavior changes. Do not weaken safety or validation tests to make an implementation pass.
