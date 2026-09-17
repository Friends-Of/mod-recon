# Architecture

Mod Recon uses the public ReforgerMods V2 API as its upstream source. The API supplies server discovery results, server-reported manifests, and Workshop version metadata.

```text
ReforgerMods API -> Poll / Validate -> Candidate Manifest -> Confirmation
                                      -> Accepted Manifest -> Diff -> Change Event
                                                               |-> Enrichment
                                                               |-> Discord
                                                               |-> History
```

## Discovery and configuration

`modrecon find` searches public servers and filters results locally against the requested name. `modrecon add` verifies the selected server ID with a fresh upstream response, then writes a YAML entry containing an environment-variable reference for the webhook. Configuration is file-based; each entry has a display name, upstream server ID, and Discord destination.

## Polling and validation

The engine creates one worker per configured server. Workers poll independently at the configured interval. Responses must have a successful envelope, fresh dataset flags, matching server identity, a complete mod count, unique 16-character Workshop IDs, and nonempty versions. A manifest is normalized as sorted uppercase mod IDs paired with exact versions; display names and ordering do not affect its digest.

## Candidate and accepted manifests

The first valid observation becomes a silent accepted baseline. A different valid digest is stored as a candidate. Only the configured number of consecutive observations with the same digest promotes it to accepted. A return to the accepted digest clears the candidate. Offline, stale, malformed, incomplete, or failed observations are rejected and cannot produce removals.

Promotion compares the old and new accepted manifests. Items are classified as added, removed, or updated by Workshop ID and version. The resulting change items and event are persisted in SQLite before delivery.

## Enrichment and authority

The **server manifest** answers what the server reports that it is using. It determines baseline state, confirmation, and diffs.

**Workshop metadata** includes descriptions, changelogs, package sizes, game versions, and timestamps fetched for added or updated versions. It enriches a change event; it cannot change the manifest or create a change on its own. Missing metadata is recorded as unavailable.

## Persistence and delivery

SQLite stores server state, accepted and candidate snapshots, snapshot mods, change items, and change events. Event payloads are persisted so a restart can retry the same notification. Discord messages use a compact embed for large deployments and a text attachment for the full recorded list. Delivery failures remain queued with retry timing.

Each server owns its state and event history. Request budgeting is shared across workers, and posts sharing one webhook are serialized with a cooldown. A database lock prevents two v0.2 processes from using the same database concurrently.

For limits, restart behavior, migrations, and troubleshooting, see [Operations](OPERATIONS.md).
