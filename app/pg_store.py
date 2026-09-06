# -*- coding: utf-8 -*-
# type: ignore
"""Postgres backend for the Store contract (cloud runners + serverless).

Same tables, same semantics as the SQLite Store, spoken over psycopg (v3).
Used when STATE_DB / DATABASE_URL is a postgres:// URL — e.g. Neon's free
tier from GitHub Actions or Vercel serverless functions.

Notes that matter:

  * One connection per Store instance. GitHub Actions and serverless are both
    single-threaded per invocation, so a pool is unnecessary weight; instead
    the connection is lazy and transparently re-opened if the server closed it
    (serverless platforms aggressively reap idle sockets).
  * Timestamps default server-side with now(); the queries remain portable.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .store import StoreBase, _json, _loads

logger = logging.getLogger("ycradar.pgstore")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    dedup_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (now()::text)
);
CREATE TABLE IF NOT EXISTS pending_early (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (now()::text),
    last_seen_at TEXT NOT NULL DEFAULT (now()::text)
);
CREATE TABLE IF NOT EXISTS message_ts (
    identity TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    ts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (now()::text)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS directory (
    source TEXT NOT NULL,
    slug TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (now()::text),
    PRIMARY KEY (source, slug)
);
"""


class PostgresStore(StoreBase):
    def __init__(self, url: str) -> None:
        self._url = url
        self._conn = None
        self._init_schema()

    # -- connection ----------------------------------------------------------
    def _connect(self):
        import psycopg

        return psycopg.connect(self._url, autocommit=True)

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")  # liveness probe; serverless sockets die
        except Exception:  # noqa: BLE001
            logger.info("pg connection lost; reopening")
            self._conn = self._connect()
            cur = self._conn.cursor()
        return cur

    def _init_schema(self) -> None:
        cur = self._cursor()
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                cur.execute(stmt)

    # -- seen ----------------------------------------------------------------
    def is_seen(self, dedup_key: str) -> bool:
        cur = self._cursor()
        cur.execute("SELECT 1 FROM seen WHERE dedup_key=%s", (dedup_key,))
        return cur.fetchone() is not None

    def mark_seen(self, dedup_key: str, payload: dict | None = None) -> None:
        cur = self._cursor()
        cur.execute(
            "INSERT INTO seen (dedup_key, payload) VALUES (%s, %s) "
            "ON CONFLICT (dedup_key) DO NOTHING",
            (dedup_key, _json(payload or {})),
        )

    # -- pending -------------------------------------------------------------
    def is_pending(self, key: str) -> bool:
        cur = self._cursor()
        cur.execute("SELECT 1 FROM pending_early WHERE key=%s", (key,))
        return cur.fetchone() is not None

    def add_pending(self, key: str, payload: dict) -> None:
        cur = self._cursor()
        cur.execute(
            "INSERT INTO pending_early (key, payload) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET payload=EXCLUDED.payload, "
            "last_seen_at=now()::text",
            (key, _json(payload)),
        )

    def remove_pending(self, key: str) -> None:
        cur = self._cursor()
        cur.execute("DELETE FROM pending_early WHERE key=%s", (key,))

    def list_pending(self) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT key, payload, first_seen_at, last_seen_at FROM pending_early"
        )
        return [
            {
                "key": r[0],
                "payload": _loads(r[1]),
                "first_seen_at": r[2],
                "last_seen_at": r[3],
            }
            for r in cur.fetchall()
        ]

    # -- message ts ----------------------------------------------------------
    def save_message_ts(self, identity: str, channel: str, ts: str) -> None:
        cur = self._cursor()
        cur.execute(
            "INSERT INTO message_ts (identity, channel, ts) VALUES (%s, %s, %s) "
            "ON CONFLICT (identity) DO UPDATE SET channel=EXCLUDED.channel, ts=EXCLUDED.ts",
            (identity, channel, ts),
        )

    def get_message_ts(self, identity: str) -> tuple[str, str] | None:
        cur = self._cursor()
        cur.execute("SELECT channel, ts FROM message_ts WHERE identity=%s", (identity,))
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

    # -- kv ------------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        cur = self._cursor()
        cur.execute("SELECT v FROM kv WHERE k=%s", (key,))
        row = cur.fetchone()
        return _loads(row[0]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        cur = self._cursor()
        cur.execute(
            "INSERT INTO kv (k, v) VALUES (%s, %s) "
            "ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v",
            (key, _json(value)),
        )

    # -- directory -----------------------------------------------------------
    def save_directory_set(self, source: str, entries: list[dict]) -> int:
        seen_slugs: set[str] = set()
        unique: list[dict] = []
        for e in entries:
            slug = (e.get("slug") or e.get("name") or "").lower()
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            unique.append({**e, "slug": slug})
        cur = self._cursor()
        cur.execute("DELETE FROM directory WHERE source=%s", (source,))
        for e in unique:
            cur.execute(
                "INSERT INTO directory (source, slug, payload) VALUES (%s, %s, %s)",
                (source, e["slug"], _json(e)),
            )
        return len(unique)

    def directory_slugs(self, source: str) -> set[str]:
        cur = self._cursor()
        cur.execute("SELECT slug FROM directory WHERE source=%s", (source,))
        return {r[0] for r in cur.fetchall()}

    def directory_counts(self) -> dict[str, int]:
        cur = self._cursor()
        cur.execute("SELECT source, COUNT(*) FROM directory GROUP BY source")
        return {r[0]: r[1] for r in cur.fetchall()}

    def seen_count(self) -> int:
        cur = self._cursor()
        cur.execute("SELECT COUNT(*) FROM seen")
        return int(cur.fetchone()[0])

    def recent_directory(self, limit: int = 8) -> list[dict]:
        cur = self._cursor()
        cur.execute(
            "SELECT source, slug, payload, updated_at FROM directory "
            "ORDER BY updated_at DESC, source LIMIT %s",
            (limit,),
        )
        return [
            {"source": r[0], "slug": r[1], "payload": _loads(r[2]), "updated_at": r[3]}
            for r in cur.fetchall()
        ]

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
