PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS servers (
 id TEXT PRIMARY KEY, upstream_server_id TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, last_accepted_snapshot_id TEXT REFERENCES manifest_snapshots(id),
 candidate_snapshot_id TEXT REFERENCES manifest_snapshots(id), candidate_seen_count INTEGER DEFAULT 0,
 last_poll_at TEXT, last_poll_status TEXT, last_error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifest_snapshots (
 id TEXT PRIMARY KEY, server_id TEXT NOT NULL REFERENCES servers(id), observed_at TEXT NOT NULL,
 upstream_collected_at TEXT, upstream_snapshot_age_seconds REAL, game_version TEXT,
 scenario_id TEXT, scenario_name TEXT, mod_count INTEGER NOT NULL, manifest_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('candidate','accepted'))
);
CREATE TABLE IF NOT EXISTS snapshot_mods (
 snapshot_id TEXT NOT NULL REFERENCES manifest_snapshots(id) ON DELETE CASCADE,
 mod_id TEXT NOT NULL, mod_name TEXT, mod_version TEXT NOT NULL, ordinal INTEGER,
 PRIMARY KEY(snapshot_id,mod_id)
);
CREATE TABLE IF NOT EXISTS change_events (
 id TEXT PRIMARY KEY, server_id TEXT NOT NULL REFERENCES servers(id),
 previous_snapshot_id TEXT NOT NULL REFERENCES manifest_snapshots(id),
 new_snapshot_id TEXT NOT NULL UNIQUE REFERENCES manifest_snapshots(id), detected_at TEXT NOT NULL,
 added_count INTEGER NOT NULL, removed_count INTEGER NOT NULL, updated_count INTEGER NOT NULL,
 delivered_at TEXT, delivery_error TEXT, delivery_next_at REAL DEFAULT 0,
 enrichment_done INTEGER DEFAULT 0, payload_json TEXT
);
CREATE TABLE IF NOT EXISTS change_items (
 event_id TEXT NOT NULL REFERENCES change_events(id), change_type TEXT NOT NULL,
 mod_id TEXT NOT NULL, mod_name TEXT, before_version TEXT, after_version TEXT,
 size_bytes INTEGER, changelog TEXT, metadata_status TEXT NOT NULL,
 game_version TEXT, created_at TEXT, updated_at TEXT,
 PRIMARY KEY(event_id,mod_id)
);
