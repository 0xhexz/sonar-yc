# -*- coding: utf-8 -*-
# type: ignore
"""State store abstraction — SQLite locally, Postgres on cloud runners.

Why this exists: GitHub Actions runners have no persistent disk between runs,
and serverless platforms (Vercel) have none between requests. SQLite is perfect
for a single always-on process, useless for the cloud path. Rather than
splitting the codebase in two, the Store now speaks either dialect:

  * ``STATE_DB`` unset or a file path      -> SQLite (unchanged behaviour)
  * ``STATE_DB=postgres://...`` or ``STATE_DB=postgresql://...`` -> Postgres

The Postgres dialect uses psycopg (v3) with the same SQL — everything in our
schema is portable (TEXT/INTEGER, simple upserts). Connection params come from
the URL; a small pool keeps serverless cold-starts cheap.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("ycradar.store")

_LOCK = threading.Lock()


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and url.startswith(("postgres://", "postgresql://"))


def open_store() -> "Store":
    """Factory used by the app and cloud entrypoints."""
    url = os.environ.get("STATE_DB") or os.environ.get("DATABASE_URL")
    if is_postgres_url(url):
        from .pg_store import PostgresStore

        return PostgresStore(url)  # type: ignore[return-value]
    db_path = url or os.environ.get("YCRADAR_DB") or "data/state.db"
    return Store(Path(db_path))


def _json(v) -> str:
    return json.dumps(v)


def _loads(v):
    if v is None:
        return {}
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return {}


class StoreBase(ABC):
    """Contract shared by SQLite and Postgres backends."""

    @abstractmethod
    def is_seen(self, dedup_key: str) -> bool: ...
    @abstractmethod
    def mark_seen(self, dedup_key: str, payload: dict | None = None) -> None: ...
    @abstractmethod
    def is_pending(self, key: str) -> bool: ...
    @abstractmethod
    def add_pending(self, key: str, payload: dict) -> None: ...
    @abstractmethod
    def remove_pending(self, key: str) -> None: ...
    @abstractmethod
    def save_message_ts(self, identity: str, channel: str, ts: str) -> None: ...
    @abstractmethod
    def get_message_ts(self, identity: str) -> tuple[str, str] | None: ...
    @abstractmethod
    def list_pending(self) -> list[dict]: ...
    @abstractmethod
    def get_state(self, key: str, default: Any = None) -> Any: ...
    @abstractmethod
    def set_state(self, key: str, value: Any) -> None: ...
    @abstractmethod
    def save_directory_set(self, source: str, entries: list[dict]) -> int: ...
    @abstractmethod
    def directory_slugs(self, source: str) -> set[str]: ...
    @abstractmethod
    def directory_counts(self) -> dict[str, int]: ...
    @abstractmethod
    def seen_count(self) -> int: ...
    @abstractmethod
    def recent_directory(self, limit: int = 8) -> list[dict]: ...
    @abstractmethod
    def close(self) -> None: ...


class Store(StoreBase):
    """SQLite backend — byte-for-byte the original behaviour."""

    def __init__(self, db_path: Path) -> None:
        import sqlite3

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with _LOCK:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen (
                    dedup_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS pending_early (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS message_ts (
                    identity TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS kv (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS directory (
                    source TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (source, slug)
                );
                """
            )

    # ---- seen / de-duplication ---------------------------------------------
    def is_seen(self, dedup_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen WHERE dedup_key=?", (dedup_key,)
        ).fetchone()
        return row is not None

    def mark_seen(self, dedup_key: str, payload: dict | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO seen (dedup_key, payload) VALUES (?, ?)",
                (dedup_key, json.dumps(payload or {})),
            )

    def is_pending(self, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM pending_early WHERE key=?", (key,)
        ).fetchone()
        return row is not None

    def add_pending(self, key: str, payload: dict) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO pending_early (key, payload) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
                "last_seen_at=datetime('now')",
                (key, json.dumps(payload)),
            )

    def remove_pending(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM pending_early WHERE key=?", (key,))

    def save_message_ts(self, identity: str, channel: str, ts: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO message_ts (identity, channel, ts) VALUES (?, ?, ?)",
                (identity, channel, ts),
            )

    def get_message_ts(self, identity: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT channel, ts FROM message_ts WHERE identity=?", (identity,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def list_pending(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT key, payload, first_seen_at, last_seen_at FROM pending_early"
        ).fetchall()
        return [
            {
                "key": r["key"],
                "payload": json.loads(r["payload"]),
                "first_seen_at": r["first_seen_at"],
                "last_seen_at": r["last_seen_at"],
            }
            for r in rows
        ]

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["v"])
        except Exception:
            return row["v"]

    def set_state(self, key: str, value: Any) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, json.dumps(value)),
            )

    def save_directory_set(self, source: str, entries: list[dict]) -> int:
        """(Re)write the known-company set for a source. Dedupes by slug. Returns count saved."""
        seen_slugs: set[str] = set()
        unique: list[dict] = []
        for e in entries:
            slug = e.get("slug") or e.get("name") or ""
            slug = slug.lower()
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            unique.append({**e, "slug": slug})
        with self._conn:
            self._conn.execute("DELETE FROM directory WHERE source=?", (source,))
            for e in unique:
                self._conn.execute(
                    "INSERT INTO directory (source, slug, payload) VALUES (?, ?, ?)",
                    (source, e.get("slug"), json.dumps(e)),
                )
        return len(unique)

    def directory_slugs(self, source: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT slug FROM directory WHERE source=?", (source,)
        ).fetchall()
        return {r[0] for r in rows}

    def directory_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT source, COUNT(*) FROM directory GROUP BY source"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def seen_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0])

    def recent_directory(self, limit: int = 8) -> list[dict]:
        rows = self._conn.execute(
            "SELECT source, slug, payload, updated_at FROM directory "
            "ORDER BY updated_at DESC, source LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"source": r[0], "slug": r[1], "payload": r[2], "updated_at": r[3]}
            for r in rows
        ]

    def close(self) -> None:
        with _LOCK:
            self._conn.close()
