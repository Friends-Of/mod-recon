# ChangeEvent proposal — v0.3

Status: design proposal, not an implemented API. Existing v0.2.9 polling and webhook operation remain supported. Schema versions below describe serialized events, independently of package release numbers.

## Authority and pipeline

```text
server observation -> validation -> confirmation -> diff
    -> persisted event facts -> bounded enrichment -> immutable ChangeEvent
    -> independent delivery adapters
```

Preserve `validate`, manifest hashing, candidate confirmation, accepted snapshots, and the existing transaction that commits a diff and advances accepted state. Never use Workshop publication as evidence of server adoption. Silent baselines stay silent. Version strings are opaque; compare exact strings without sorting them as semantic versions.

## Proposed serialized model

This is an illustrative shape, not a historical observation:

```json
{
  "schema_version": 1,
  "event_id": "existing-or-new-stable-uuid",
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

Keep existing normalized `change_events`, `change_items`, manifests, and snapshots as source records. Add a versioned event publication table, conceptually:

```text
event_publications(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                   event_id UNIQUE REFERENCES change_events(id),
                   schema_version, published_at, event_json)
```

Confirmation atomically persists facts and advances accepted state as today. A recoverable finalizer finds unfinalized events independently of webhook delivery. It enriches exact versions through the shared cache, bounded by existing time/request limits, computes supported groups, validates the document, then publishes once. Metadata failure must permit an event with explicit unavailable fields. Published event JSON is immutable; future corrections, if needed, require an explicit later design, not silent mutation.

Assign the publication sequence only when the ready event is inserted. A consumer must never skip an unfinished lower-sequence event because a later event became ready first. Preserve confirmation order within each server by finalizing its oldest pending event first; different servers can progress independently. A crash before publication resumes finalization; a unique event ID prevents a second publication. Commit publication and any local webhook outbox insertion atomically.

Proposed bounded library methods: `get_event(event_id)` and `iter_events(after_sequence, limit)`, returning immutable records and continuation sequence. Consumers own their durable cursor; reads do not mark globally delivered. An event can be consumed by multiple adapters. Retain publication records for the initial release; introduce pruning only with an explicit retention/cursor policy. No public HTTP service is required.

## Webhook compatibility

Add a webhook outbox with a logical destination key, event reference, frozen rendered payload, delivered timestamp, next retry time, and safe error code. A destination key refers to local configuration; never persist the webhook secret. One destination failure must not mark the event delivered for all consumers. Retain existing limits, attachments, allowed-mention suppression, donation option, and retry semantics.

Refactor `presentation.render_message` to consume ChangeEvent. It formats prepared facts and groups; it does not poll, diff, query metadata, or establish grouping semantics. Route transport and retries through a webhook adapter. Run delivery independently from polling so a slow Discord request cannot occupy the server observation worker. Keep bounded workers and shared budget/cache objects; avoid a framework or message broker.

For existing queued records, preserve frozen `payload_json` exactly, including older JSON-only payloads. Import existing delivered status into the legacy webhook destination's outbox, without resending historical messages. New programmatic consumers choose an explicit starting cursor; the default for a newly attached live consumer is current, not replay-all.

## Migration and compatibility

1. Stop writers, create and verify a backup, then rehearse the migration on a copy. Refuse startup on a schema newer than the binary understands.
2. Add a schema version ledger, publication/outbox tables, and nullable observation/provenance fields in an idempotent transaction. Preserve all existing server/event/snapshot IDs, candidates, accepted pointers, change items, and delivery errors.
3. Derive legacy first observation from the accepted candidate snapshot's `observed_at`; map legacy `detected_at` to confirmation time. Do not invent the confirmation scan timestamp, confirmation threshold, metadata retrieval time, or historical upstream name where they were not recorded. Mark missing facts explicitly. Capture them directly for new events.
4. Backfill from stored metadata without live refetches or changing event identity. Treat legacy `enrichment_done` as finalization evidence; resume unfinished work through the finalizer. Record a deterministic publication order and start new consumers after the migrated high-water mark unless replay was explicitly requested.
5. Backfill local webhook outbox status/payloads transactionally. Verify a queued event retries once while previously delivered events remain delivered. Persist destination mapping when configurations change; do not silently reroute old pending messages.
6. Keep existing YAML/CLI paths operational. Allow a delivery-free library configuration; webhook CLI setup stays convenient and continues to support multiple servers and shared destinations. Keep legacy columns during the compatibility window; publish deprecation separately.

Rollback is restoration of the verified database and matching binary while writers are stopped, not an untested reverse migration. Avoid divergent writers during rollback. v0.2.9 needs no history migration; these additive migrations belong to the future v0.3 implementation.

## Acceptance tests

Carry forward all observation, confirmation, baseline, budget, metadata, and webhook tests. Add schema validation, deterministic opaque-version diffs, unknown-vs-zero size totals, provenance, supported grouping exceptions, timestamp mapping, event immutability, concurrent finalization, cursor ordering, crash/restart publication, two independent consumers, slow-delivery isolation, and migration/restore tests using delivered and queued legacy payloads. A fake server change must produce one event with or without an enabled webhook.
