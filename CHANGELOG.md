# Changelog

Git history starts with the source as it existed on September 17, 2026. Earlier work predates version control; the entries below are documented milestones, not reconstructed commits.

## 0.3.0 — Change Events — Unreleased

- Immutable schema-v1 ChangeEvents with stable IDs, publication sequences, exact versions, nullable package sizes, captured identity/timestamps, provenance, and supported evidence-based grouping.
- Additive history migration (`PRAGMA user_version=1`) and offline `modrecon migrate`; backfill uses stored facts only, with unknown historical fields explicit.
- Independent polling, publication/enrichment, and webhook delivery workers. Legacy frozen payloads and delivered status are retained in an adapter outbox.
- Delivery-free `Core`/`Monitor` integration API, unique-server reconciliation, human-readable discovery, status, and bounded resumable event reads.
- Explicit upstream polling, discovery, verification, and enrichment purposes; optional requests cannot borrow polling's reserve.
- Crash/restart, migration rollback/idempotence, immutable publication, cursor ordering, legacy delivery, backup/restore, and blocked-network regression coverage.

The migration requires a verified backup and a stopped runner. This candidate does not imply deployment or a production database upgrade.

## 0.2.9 — Production hardening — Release candidate

Prepared for merge and release validation. The existing production soak remains on the earlier v0.2.9 build; backup-command and documentation changes do not imply a production upgrade.

- Optional environment-based ReforgerMods authentication and live quota verification; anonymous Core remains the default.
- Shared persistent exact-version enrichment cache with concurrent request coalescing and bounded failure caching.
- Runtime optional-request caps protect polling's reserved budget; one authoritative detail request per server per cycle remains unchanged.
- Staggered once-mode validation, authenticated redirect protection, and regression coverage.
- A representative five-server deployment is undergoing sustained production testing on anonymous API access.
- Public roadmap and contributor guidance focus on self-hostable Core; normalized ChangeEvents are the v0.3 integration goal.
- Added an offline `backup` command with verified SQLite copies, no-overwrite protection, active-runner refusal, and a completion marker; deployment instructions explicitly gate on failure.

## 0.2.0

- Security hardening: bounded upstream metadata and Discord attachments (including queued legacy messages), terminal control sanitization, safe bounded YAML, and webhook secrets excluded from configuration representations. Regression tests cover oversized-event delivery and queue progress.
- Validated on Python 3.14.7; explicitly close short-lived SQLite connections to prevent handle leaks.
- YAML configuration for independently monitored public servers, with shared or separate Discord webhooks and environment-variable secrets.
- Installable `modrecon find`, `add`, `check`, `run`, and `status` commands.
- Server search handles WCS punctuation and distinguishes NA1 from NA10. Adding verifies identity without posting; offline servers can be configured.
- Independent worker scheduling, configurable confirmation count, persisted shared upstream request budget, and per-webhook pacing.
- Existing SQLite baselines/history are reusable; duplicate v0.2 processes are blocked per database.
- Tested against separate live NA7/NA1 baselines and a copy of the v0.1.1 database before production adoption.

## 0.1.1 — 2026-09-17

### Project name

- Renamed Reforger Watch to **Mod Recon**, including documentation and notification branding.
- Repository renamed to `Friends-Of/mod-recon`.
- Existing database filenames, installation path, and scheduled-task name are retained to preserve uninterrupted collection and history. The updated service is deployed.

### Presentation update

- Compact notifications for deployments with more than 12 changes.
- Exact-version WCS/RHS groupings with visible version exceptions.
- Full change-list attachments with IDs, versions, and stored metadata.
- Package-size wording: “12.34 GiB across 41 changed packages.”
- Author changelogs in the full report rather than the compact notification.
- Read-only previews and presentation/attachment tests.
- Activated the **project donation link** below notifications and in the README.
- Initial public README, MIT license, and Git tracking; secrets, runtime data, generated previews, and local operations notes excluded.

Deployed after a local SQLite backup and a labeled Discord preview. All 18 tests passed; the restarted service completed a successful NA7 poll with its accepted baseline preserved and no pending events. Collection logic and stored history are unchanged.

## 0.1 — 2026-09-17

- One configured server, initially WCS NA7, polled every 120 seconds.
- Strict response validation and two-observation change confirmation.
- SQLite snapshots, baseline, candidate, and event history.
- Exact-version enrichment and Discord delivery with persisted retries.
- Background launcher with rotating logs and scheduled startup for the initial deployment.
- First real confirmed deployment: 3 additions, 42 updates, and 1 removal.

This documents the running collector baseline; it is not a Git release tag.
