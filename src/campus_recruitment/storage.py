"""SQLite upserts, revision history, run audit, and calendar mapping."""
import hashlib
import json
import sqlite3
import uuid
from dataclasses import fields
from pathlib import Path

from .models import Company, Fair, Position


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value):
    return hashlib.sha256(stable_json(value).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                kind TEXT NOT NULL, entity_key TEXT NOT NULL, payload TEXT NOT NULL,
                content_hash TEXT NOT NULL, first_seen TEXT NOT NULL, last_checked TEXT NOT NULL,
                revision INTEGER NOT NULL, PRIMARY KEY(kind, entity_key));
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, entity_key TEXT NOT NULL,
                change_type TEXT NOT NULL, checked_at TEXT NOT NULL,
                previous TEXT, current TEXT NOT NULL, run_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, mode TEXT NOT NULL,
                metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calendar_map (
                calendar_key TEXT NOT NULL, fair_id TEXT NOT NULL, event_id TEXT NOT NULL,
                content_hash TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(calendar_key, fair_id));
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def key(kind, payload):
        if kind == "fair":
            return payload["job_fair_id"]
        if kind == "company":
            return payload["company_id"]
        if kind == "position":
            return stable_json([payload["job_fair_id"], payload["company_id"], payload["position_id"]])
        raise ValueError("Unknown entity kind")

    def _upsert(self, kind, payload, run_id, checked_at):
        key = self.key(kind, payload)
        previous = self.db.execute("SELECT * FROM entities WHERE kind=? AND entity_key=?", (kind, key)).fetchone()
        old = json.loads(previous["payload"]) if previous else None
        if old and old["provenance"] != payload["provenance"]:
            raise ValueError("Synthetic and real data require separate databases")
        if old and kind == "company":
            payload["related_job_fair_ids"] = sorted(set(old["related_job_fair_ids"] + payload["related_job_fair_ids"]))
        # Explicit trusted cancellation may retain previously observed times, never guessed times.
        if old and kind == "fair" and payload["status"] == "cancelled" and payload["status_verified"]:
            for name in ("start_time", "end_time", "start_date"):
                if payload[name] is None:
                    payload[name] = old[name]
        content = {k: v for k, v in payload.items() if k != "last_checked_at"}
        digest = content_hash(content)
        changed = previous is None or previous["content_hash"] != digest
        revision = previous["revision"] + int(changed) if previous else 0
        first_seen = previous["first_seen"] if previous else checked_at
        self.db.execute("""INSERT INTO entities VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(kind,entity_key) DO UPDATE SET payload=excluded.payload,
                content_hash=excluded.content_hash,last_checked=excluded.last_checked,revision=excluded.revision""",
            (kind, key, stable_json(payload), digest, first_seen, checked_at, revision))
        if not changed:
            return None
        change = "added"
        if old:
            change = "updated"
            if kind == "fair":
                if payload["status"] == "cancelled" and payload["status_verified"] and old["status"] != "cancelled":
                    change = "cancelled"
                elif (old["start_time"], old["end_time"]) != (payload["start_time"], payload["end_time"]):
                    change = "rescheduled"
        self.db.execute("INSERT INTO history(kind,entity_key,change_type,checked_at,previous,current,run_id) VALUES(?,?,?,?,?,?,?)",
                        (kind, key, change, checked_at, stable_json(old) if old else None, stable_json(payload), run_id))
        return {"kind": kind, "entity_key": key, "change_type": change, "checked_at": checked_at, "current": payload, "previous": old}

    def apply(self, snapshot):
        run_id = uuid.uuid4().hex
        changes = []
        # Fold duplicate IDs before writing history; conflicting duplicates are not silently discarded.
        normalized = {}
        for kind, models in (("fair", snapshot.fairs), ("company", snapshot.companies), ("position", snapshot.positions)):
            for model in models:
                payload = model.as_dict()
                key = (kind, self.key(kind, payload))
                if key in normalized:
                    old = normalized[key]
                    if kind == "company":
                        if old["company_name"] != payload["company_name"]:
                            raise ValueError("Conflicting names for one company ID")
                        old["related_job_fair_ids"] = sorted(set(old["related_job_fair_ids"] + payload["related_job_fair_ids"]))
                    elif old != payload:
                        raise ValueError(f"Conflicting duplicate {kind} ID")
                else:
                    normalized[key] = payload
        metadata = {"complete": snapshot.complete, "query_verified": snapshot.query_verified,
                    "errors": snapshot.errors, "warnings": snapshot.warnings,
                    "counts": {"fairs": len(snapshot.fairs), "companies": len(snapshot.companies), "positions": len(snapshot.positions)}}
        with self.db:
            for (kind, _), payload in normalized.items():
                change = self._upsert(kind, payload, run_id, snapshot.checked_at)
                if change:
                    changes.append(change)
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?)", (run_id, snapshot.checked_at, snapshot.mode, stable_json(metadata)))
        return run_id, changes

    def list(self, kind):
        return [dict(json.loads(row["payload"]), first_seen_at=row["first_seen"],
                     last_checked_at=row["last_checked"], revision=row["revision"])
                for row in self.db.execute("SELECT * FROM entities WHERE kind=? ORDER BY entity_key", (kind,))]

    def models(self, kind):
        cls = {"fair": Fair, "company": Company, "position": Position}[kind]
        allowed = {f.name for f in fields(cls)}
        return [cls(**{k: v for k, v in row.items() if k in allowed}) for row in self.list(kind)]

    def history(self, since=None):
        rows = self.db.execute("SELECT * FROM history WHERE (? IS NULL OR checked_at>=?) ORDER BY id", (since, since))
        return [{**dict(row), "previous": json.loads(row["previous"]) if row["previous"] else None,
                 "current": json.loads(row["current"])} for row in rows]

    def runs(self, limit=30):
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in
                self.db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,))]

    def mapping(self, calendar_key, fair_id):
        row = self.db.execute("SELECT * FROM calendar_map WHERE calendar_key=? AND fair_id=?", (calendar_key, fair_id)).fetchone()
        return dict(row) if row else None

    def save_mapping(self, calendar_key, fair_id, event_id, digest, revision, status):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO calendar_map VALUES(?,?,?,?,?,?)",
                            (calendar_key, fair_id, event_id, digest, revision, status))

    def backup(self, destination, *, public=False):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.db.backup(target)
            if public:
                from .reports import public_data
                target.execute("DELETE FROM calendar_map")
                for table, columns in (("entities", ["payload"]), ("history", ["previous", "current"]), ("runs", ["metadata"])):
                    for column in columns:
                        for row_id, text in target.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                            if text:
                                target.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                                               (stable_json(public_data(json.loads(text))), row_id))
