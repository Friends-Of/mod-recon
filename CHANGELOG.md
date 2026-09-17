# Changelog

Git history starts with the source as it existed on September 17, 2026. Earlier work predates version control; the entries below are documented milestones, not reconstructed commits.

## Unreleased

### Project name

- Renamed Reforger Watch to **Mod Recon**, including documentation and notification branding.
- Repository renamed to `Friends-Of/mod-recon`.
- Existing database filenames, installation path, and scheduled-task name are retained to preserve uninterrupted collection and history. Branding loads with the next planned service update.

### Pending presentation update

- Compact notifications for deployments with more than 12 changes.
- Exact-version WCS/RHS groupings with visible version exceptions.
- Full change-list attachments with IDs, versions, and stored metadata.
- Package-size wording: “12.34 GiB across 41 changed packages.”
- Author changelogs in the full report rather than the compact notification.
- Read-only previews and presentation/attachment tests.
- Optional **project donation link** below notifications.
- Initial public README, MIT license, and Git tracking; secrets, runtime data, generated previews, and local operations notes excluded.

Presentation changes are prepared but not loaded by the existing hosted process. The collector remains uninterrupted while the project donation link is prepared. Collection logic and stored history are unchanged.

## 0.1 — 2026-09-17

- One configured server, initially WCS NA7, polled every 120 seconds.
- Strict response validation and two-observation change confirmation.
- SQLite snapshots, baseline, candidate, and event history.
- Exact-version enrichment and Discord delivery with persisted retries.
- Background launcher with rotating logs and scheduled startup for the initial deployment.
- First real confirmed deployment: 3 additions, 42 updates, and 1 removal.

This documents the running collector baseline; it is not a Git release tag.
