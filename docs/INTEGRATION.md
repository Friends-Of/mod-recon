# Core integration API — v0.3

Core is self-hostable and delivery-independent. The library owns canonical observation, validation, confirmation, history, enrichment, and event publication. Consumers own their destination routing and durable read cursor.

```python
import os
import time
from modrecon.core import Core, Monitor

with Core("data/core.db", api_key=os.getenv("REFORGERMODS_API_KEY")) as core:
    matches, total_pages = core.search_servers("Everon Alpha", page=1)
    # Let the caller choose a verified ID from matches; never assume the first match.
    selected_id = "persistent-id-chosen-by-the-caller"
    core.apply_monitor_set([Monitor(selected_id, "Everon Alpha", 120)])
    cursor = 0  # Load the consumer's saved cursor instead on restart.
    while True:
        for event in core.iter_events(after_sequence=cursor, limit=100):
            # Transactionally store/act on event_id and advance your durable cursor.
            print(event["event_id"], event["updated"])
            cursor = event["sequence"]
        time.sleep(1)
```

The sample is schematic: choose the actual server from lookup results and replace the print with durable consumer processing. Repeated reads are safe; exactly-once external side effects are the consumer's responsibility. Historical reads do not authorize posting old events to new destinations.

## Construction and lifetime

`Core(database_path, *, confirmation_polls=2, requests_per_minute=50, requests_per_day=5000, api_key=None)` is a context manager. It owns one process lock, a shared upstream client/budget/cache, and worker registry. Context entry initializes/migrates history; exit stops and joins workers and releases the lock. Backup and migration rules in [Operations](OPERATIONS.md) apply before using a production file. `api=` is a testing seam for deterministic fake upstream responses.

No key is required. An explicitly supplied key triggers live quota verification before monitoring. The library does not implicitly read `.env`; applications may pass the environment value as shown. CLI `.env` behavior is unchanged. Do not run multiple Core instances against the same history database.

## Operations

| Operation | Result and behavior |
|---|---|
| `search_servers(query, page=1)` | `(matches, total_pages)` using the existing name/token matching and budgeted discovery; query length 1–180, positive page |
| `apply_monitor_set(monitors)` | Reconciles the complete desired list of `Monitor(server_id, display_name, poll_interval=120)` and returns the desired ID tuple; empty list pauses all |
| `get_status(server_id)` | Stored status dict or `None`; includes enabled, last poll status/time/error, accepted snapshot reference and candidate count |
| `get_event(event_id)` | Detached schema-v1 event dict, or `None` if absent/not yet published |
| `iter_events(after_sequence=0, limit=100)` | Ordered list of at most 1–1,000 events strictly after cursor; use the last returned sequence for the next page |

For read-only event access without owning a monitor, use `EventReader(database_path)` from `modrecon.events`; it exposes the last two methods, opens SQLite read-only, and never migrates or contacts upstream. It requires an already migrated database.

Reconciliation validates the entire desired set before changing anything. Duplicate IDs, invalid identities/names, intervals below 120 seconds, and schedules exceeding 80% of the configured daily/minute budget are rejected. Concurrent calls are serialized. Unchanged workers remain alive; changed/removed workers stop and join before replacement, so one upstream ID never has overlapping pollers in this process. New workers are staggered at the shared request pacing interval. Cadence/name changes do not recreate the server record or baseline. Removed monitors retain history and are marked disabled; re-adding enables the same record. The caller stores its desired set and reapplies it after process restart.

Status is stored state, not a claim that a process is currently alive. A record retains its last poll while disabled or while its host is stopped. Check service health separately. Admission is based on configured allowance; other applications sharing an upstream IP/account can still consume quota.

## Publication and cursor rules

Publication sequence is assigned only when the immutable event is committed. A slow event on one server can appear after another server's event; it then receives a later sequence, so a saved cursor cannot skip it. Within each server, finalization proceeds in confirmation order. Do not order cursors by timestamps or UUIDs. An empty page leaves the existing cursor unchanged.

Zero starts at historical events. For live-only attachment, first drain existing pages without dispatching and persist that high-water mark; then begin normal consumption. Consumers should also apply their own start-time eligibility rules when adding destinations. Unknown schema versions raise rather than silently skipping data.

An event and local webhook outbox row commit atomically. Retry after a crash before commit republishes the same event ID once; retry after commit finds the existing publication. Pending enrichment survives restart and is bounded; unavailable metadata still permits publication. Legacy migration never invents missing facts or performs historical metadata requests.

## Request purposes

The shared API's `server(id)` is reserved for authoritative polling. `search(query,page)` is discovery; `verify_server(id)` is optional selection/detail verification; `version(mod_id,version)` is optional enrichment. Direct `get(path)` defaults to optional work. These explicit contexts replace URL-based classification, preventing detail verification from borrowing authoritative polling's reserve. The same persisted daily/minute totals and 20% optional limits apply. Exact-version cache hits make no network request.

The CLI `add` command now uses verification purpose. Consumers should use the high-level search/reconcile/event interface; they do not need to call the poller or reconstruct diffs.
