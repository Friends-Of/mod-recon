# Architecture

Mod Recon Core uses the public ReforgerMods V2 API as its upstream source. The API supplies server discovery results, server-reported manifests, and Workshop version metadata. Discovery supports choosing a monitor; it is not a general server-browser product.

```text
ReforgerMods API -> Poll / Validate -> Candidate Manifest -> Confirmation
                                      -> Accepted Manifest + persisted diff
                                      -> bounded enrichment -> ChangeEvent publication
                                                              |-> webhook outbox/adapter
                                                              |-> cursor-based consumers
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

v0.2.9 retains one authoritative server-detail request per unique configured ID per cycle. A separate SQLite metadata cache is keyed by uppercase Workshop ID and the exact version string. Workers coalesce concurrent lookups of the same key within an engine. Valid metadata lasts 24 hours; failed lookups are negatively cached for 60 seconds. Cache hits do not spend API requests. Metadata is fetched only for change enrichment, not to initialize a baseline, and never determines server adoption.

Polling and optional requests share total request accounting. Optional work (enrichment, discovery, quota checks) additionally has a hard cap of 20% of each configured minute/day allowance, protecting 80% for polling. The existing configuration check requires scheduled base polling to fit that 80%. This prevents enrichment from consuming polling's reserved capacity; it does not protect against upstream outages or other applications sharing the same IP/account quota.

For limits, restart behavior, migrations, and troubleshooting, see [Operations](OPERATIONS.md).

## Event boundary in v0.3

The proven observation/confirmation transaction is retained. A separate per-server worker finalizes stored facts through bounded enrichment and publishes an immutable event in `event_publications`. Publication sequence and webhook outbox insertion commit together. Another independent worker consumes that outbox; its network waits never run in the polling thread. A server without a webhook still publishes events. `Watch.process_pending` remains a synchronous compatibility helper, not the persistent engine's scheduling path.

Core grouping rules produce facts with member IDs, exact version strings, and evidence; presentation formats them. Legacy event delivery columns are mirrored for compatibility, but the webhook outbox owns transport state. New consumers read events without mutating global delivery state. See the [ChangeEvent contract](CHANGE_EVENTS.md) and [integration API](INTEGRATION.md). Core retains full monitoring, history, metadata, and webhook capabilities for self-hosters.

`SharedAPI` sets explicit request purposes: authoritative `server` polling, `search` discovery, `verify_server` selection verification, and exact-version enrichment. Direct `get` calls default to optional work, regardless of URL. All non-poll purposes use the optional quota allowance. Library monitor reconciliation also validates the sum of individual cadences against the same reserved allowance.
