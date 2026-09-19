# ChangeEvent contract — v0.3

Status: implemented release candidate, not yet deployed to production. Existing v0.2.9 polling and webhook behavior remain supported. Schema versions below describe serialized events, independently of package release numbers. The history database uses `PRAGMA user_version=1`.

## Authority and pipeline

```text
server observation -> validation -> confirmation -> diff
    -> persisted event facts -> bounded enrichment -> immutable ChangeEvent
    -> independent delivery adapters
```

Preserve `validate`, manifest hashing, candidate confirmation, accepted snapshots, and the existing transaction that commits a diff and advances accepted state. Never use Workshop publication as evidence of server adoption. Silent baselines stay silent. Version strings are opaque; compare exact strings without sorting them as semantic versions.

## Serialized model (schema version 1)

This is an illustrative shape, not a historical observation:

```json
{
  "schema_version": 1,
  "event_id": "existing-or-new-stable-uuid",
  "sequence": 1,
  "server_id": "persistent-upstream-id",
  "server_name": "Everon Alpha",
  "server_display_name": "Example Conflict #1",
  "observed_at": "2026-09-18T12:00:00Z",
  "confirmed_at": "2026-09-18T12:02:00Z",
  "previous_manifest": {"snapshot_id": "previous-uuid", "sha256": "digest"},
  "new_manifest": {"snapshot_id": "new-uuid", "sha256": "digest"},
  "added": [],
  "removed": [],
  "updated": [{
    "mod_id": "AAAAAAAAAAAAAAAA",
    "name": "Example Equipment",
    "old_version": "build-blue",
    "new_version": "release-A",
    "old_package_size_bytes": null,
    "new_package_size_bytes": 1048576,
    "metadata": {
      "status": "available",
      "source": "reforgermods.v2.mod_version",
      "requested_version": "release-A",
      "retrieved_at": "2026-09-18T12:02:01Z",
      "reason": null,
      "changelog": null,
      "game_version": null,
      "upstream_created_at": null,
      "upstream_updated_at": null
    }
  }],
  "changed_package_sizes": {
    "total_bytes": 1048576,
    "known_packages": 1,
    "eligible_packages": 1,
    "missing_packages": 0,
    "basis": "added_and_updated_new_versions"
  },
  "groups": [],
  "provenance": {
    "manifest_source": "reforgermods.v2.server_detail",
    "upstream_server_id": "persistent-upstream-id",
    "first_upstream_collected_at": "2026-09-18T11:59:40Z",
    "confirmation_upstream_collected_at": "2026-09-18T12:01:40Z",
    "confirmation_observations": 2,
    "legacy_backfill": false,
    "unknown_fields": []
  }
}
```

All timestamps are UTC with explicit timezone. `observed_at` is the first valid observation in the consecutive candidate sequence that was eventually accepted; `confirmed_at` is the observation that met the threshold. Neither claims the actual server installation time. Preserve upstream collection timestamps separately and allow null when unavailable. Existing confirmation counts local valid observations; v0.3 does not silently redefine these as distinct upstream scans.

`server_id` is the persistent upstream ID; the existing SQLite internal server UUID remains an implementation detail. Capture the upstream name and configured display name at confirmation. A later rename must not rewrite historical events. Legacy data may only have a display label: record that limitation rather than inventing an old upstream name.

Each mod has uppercase Workshop ID, name, exact versions, nullable package sizes, and metadata/provenance. Added items have null `old_version`; removed items have null `new_version`; updated items have both. Change identity is `(event_id, mod_id)`, sorted deterministically by mod ID within each array. Names come from the confirmed snapshot (removed items from the previous snapshot).

Metadata status is `available`, `unavailable`, or `not_requested`, with a safe reason code for failure. Preserve provider null fields as unknown. Cache records need the original retrieval time; a cache hit must not pretend to be a new retrieval. Legacy cache/event records without retrieval time use null with a provenance note. Store no keys, webhook URLs, or raw HTTP exception bodies. Author changelogs are attributed source text, not independently verified explanations.

The size aggregate sums known sizes of added/updated new versions once each. Removed packages are excluded. Zero is known zero; null is unknown. Do not fetch old sizes just to manufacture a delta. Never label these totals actual download size: compression, patching, and client cache state are not known. Existing wording remains “GiB across N changed packages,” with missing coverage shown.

Groups are optional facts with `rule_id`, `rule_version`, explicit member mod IDs, exact target version, and evidence. Move the current supported literal package-name-prefix/version grouping out of Discord rendering into a small pure Core grouping function, retaining its thresholds and exceptions. A shared prefix does not establish operator ownership, dependency, compatibility, or release contents. Do not invent semantic version families or infer groups when evidence is insufficient. No extra network calls are needed for grouping.

## Persistence and consumption

Existing normalized `change_events`, `change_items`, manifests, and snapshots remain source records. The additive migration adds nullable captured identity/provenance fields to events, `metadata_retrieved_at` to items, a webhook outbox, and this publication table:

```text
event_publications(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                   event_id UNIQUE REFERENCES change_events(id),
                   schema_version, published_at, event_json)
```

Confirmation atomically persists facts and advances accepted state as today. A recoverable finalizer finds unfinalized events independently of webhook delivery. It enriches exact versions through the shared cache, bounded by existing time/request limits, computes supported groups, validates the document, then publishes once. Metadata failure must permit an event with explicit unavailable fields. Published event JSON is immutable; future corrections, if needed, require an explicit later design, not silent mutation.

Assign the publication sequence only when the ready event is inserted. A consumer must never skip an unfinished lower-sequence event because a later event became ready first. Preserve confirmation order within each server by finalizing its oldest pending event first; different servers can progress independently. A crash before publication resumes finalization; a unique event ID prevents a second publication. Commit publication and any local webhook outbox insertion atomically.

Library methods: `get_event(event_id)` and `iter_events(after_sequence=0, limit=100)`. A page is a list of detached documents; its last event's `sequence` is the continuation cursor. An empty page retains the caller's previous cursor. Limit is 1–1,000. Zero explicitly denotes the beginning of stored history; a new live-only consumer first drains existing pages without acting on them, persists the resulting cursor, then processes future pages. Reads do not mark globally delivered. Unknown event-schema versions fail explicitly. SQLite triggers prohibit mutation/deletion of publications; callers may freely mutate returned copies. No public HTTP service is required.

## Webhook compatibility

`webhook_outbox` stores a destination fingerprint, event reference, frozen rendered payload, delivered timestamp, next retry time, and safe error. The fingerprint is SHA-256 of the configured webhook URL, not the secret itself. Changed URLs cannot silently receive old pending messages. Legacy rows initially use a per-server placeholder, bound once when a watcher starts with the verified unchanged configuration. One destination failure does not mark the event delivered for external consumers. Existing limits, attachments, allowed-mention suppression, donation option, and retry semantics remain intact.

`presentation.render_change_event` consumes ChangeEvent and formats prepared facts/groups through the compatible renderer. It does not poll, diff, or query metadata. Grouping lives in Core. `modrecon.webhook` owns delivery and retries. Persistent engines allocate polling, finalization, and (when configured) webhook threads separately per server, with shared budget/cache objects. Once-mode is finite and performs those stages sequentially for each server; it does not promise background polling while a one-shot delivery is in progress.

Existing queued records preserve frozen `payload_json` exactly, including older JSON-only payloads. Existing delivered status is imported into the legacy webhook destination's outbox, without resending historical messages. Programmatic consumers control replay through their own cursor and must not treat the initial history read as permission to notify new destinations.

## Migration and compatibility

1. Stop writers, create and verify a backup, then rehearse the migration on a copy. Refuse startup on a schema newer than the binary understands.
2. `modrecon.storage.migrate` uses a `PRAGMA user_version` ledger, publication/outbox tables, and nullable observation/provenance fields in an idempotent transaction. All existing server/event/snapshot IDs, candidates, accepted pointers, change items, and delivery errors are preserved. `modrecon migrate` exposes this offline operation under the process lock. Starting v0.3 also runs the same migration, so production must be backed up before any new runner is started.
3. Derive legacy first observation from the accepted candidate snapshot's `observed_at`; map legacy `detected_at` to confirmation time. Do not invent the confirmation scan timestamp, confirmation threshold, metadata retrieval time, or historical upstream name where they were not recorded. Mark missing facts explicitly. Capture them directly for new events.
4. Backfill every legacy event from stored metadata without live refetches or changing event identity, ordered by stored confirmation timestamp then event ID. Legacy unfinished enrichment is frozen as the stored `not_requested`/unknown facts and marked finalized; it does not trigger an API fetch. This implements the no-historical-refetch requirement. New v0.3 interrupted enrichment resumes normally. Unknown historical upstream/display names remain null rather than adopting a possibly renamed current label. The webhook adapter may use its current configured label when no frozen legacy payload exists.
5. Backfill local webhook outbox status/payloads transactionally. Verify a queued event retries once while previously delivered events remain delivered. Persist destination mapping when configurations change; do not silently reroute old pending messages.
6. Existing YAML/CLI paths remain operational. `Core` provides delivery-free library configuration and `Monitor` contains only server ID, display name, and numeric cadence. Legacy delivery columns remain mirrored for compatibility; adapters use the outbox. See [Integration](INTEGRATION.md) for the exact API and lifecycle.

Rollback is restoration of the verified database and matching binary while writers are stopped, not an untested reverse migration. Avoid divergent writers during rollback. v0.2.9 needs no history migration; these additive migrations belong to the future v0.3 implementation.

## Acceptance tests

Carry forward all observation, confirmation, baseline, budget, metadata, and webhook tests. Add schema validation, deterministic opaque-version diffs, unknown-vs-zero size totals, provenance, supported grouping exceptions, timestamp mapping, event immutability, concurrent finalization, cursor ordering, crash/restart publication, two independent consumers, slow-delivery isolation, and migration/restore tests using delivered and queued legacy payloads. A fake server change must produce one event with or without an enabled webhook.
