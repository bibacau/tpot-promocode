"""SQLite storage: settings, product catalogue, sessions, comments and replies.

Pattern adapted from wadebot's server.py (MIT): one local SQLite file holding
session and message history. Extended with a product catalogue and an
approval/status trail for every AI reply.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sku               TEXT,
    name              TEXT NOT NULL,
    price             REAL,
    promo_price       REAL,
    stock_status      TEXT NOT NULL DEFAULT 'in_stock',
    description       TEXT NOT NULL DEFAULT '',
    features          TEXT NOT NULL DEFAULT '',
    tiktok_product_id TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    sort              INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at   REAL,
    notes      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    user       TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    status     TEXT NOT NULL,
    reason     TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS replies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    comment_id INTEGER,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    product_id INTEGER,
    status     TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    spoken_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_comments_session ON comments(session_id);
CREATE INDEX IF NOT EXISTS idx_replies_session ON replies(session_id);
"""

PRODUCT_FIELDS = (
    "sku", "name", "price", "promo_price", "stock_status", "description",
    "features", "tiktok_product_id", "active", "sort",
)
STOCK_STATUSES = ("in_stock", "low_stock", "out_of_stock")


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ── helpers ──
    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── settings ──
    def get_setting(self, key: str) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def delete_setting(self, key: str) -> None:
        self._exec("DELETE FROM settings WHERE key=?", (key,))

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._all("SELECT key, value FROM settings")}

    # ── products ──
    @staticmethod
    def _clean_product(data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in PRODUCT_FIELDS:
            if f not in data:
                continue
            v = data[f]
            if f in ("price", "promo_price"):
                v = None if v in (None, "") else round(float(v), 2)
                if v is not None and v < 0:
                    raise ValueError(f"{f} cannot be negative")
            elif f in ("active", "sort"):
                v = int(bool(v)) if f == "active" else int(v or 0)
            elif f == "stock_status":
                v = (v or "in_stock").strip().lower()
                if v not in STOCK_STATUSES:
                    raise ValueError(f"stock_status must be one of {STOCK_STATUSES}")
            else:
                v = "" if v is None else str(v).strip()
            out[f] = v
        return out

    def add_product(self, data: dict[str, Any]) -> dict:
        clean = self._clean_product(data)
        if not clean.get("name"):
            raise ValueError("Product name is required")
        clean["created_at"] = time.time()
        cols = ", ".join(clean)
        marks = ", ".join(f":{k}" for k in clean)
        cur = self._exec(f"INSERT INTO products({cols}) VALUES({marks})", clean)
        return self.get_product(cur.lastrowid)  # type: ignore[return-value]

    def update_product(self, pid: int, data: dict[str, Any]) -> dict | None:
        clean = self._clean_product(data)
        if "name" in clean and not clean["name"]:
            raise ValueError("Product name is required")
        if clean:
            sets = ", ".join(f"{k}=:{k}" for k in clean)
            self._exec(f"UPDATE products SET {sets} WHERE id=:id", {**clean, "id": pid})
        return self.get_product(pid)

    def delete_product(self, pid: int) -> bool:
        return self._exec("DELETE FROM products WHERE id=?", (pid,)).rowcount > 0

    def get_product(self, pid: int | None) -> dict | None:
        if pid is None:
            return None
        return self._one("SELECT * FROM products WHERE id=?", (pid,))

    def list_products(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM products"
        if active_only:
            sql += " WHERE active=1"
        return self._all(sql + " ORDER BY sort, id")

    def import_products_csv(self, text: str) -> int:
        """Import products from CSV with a header row using PRODUCT_FIELDS names."""
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        rows = []
        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("name"):
                continue
            try:
                rows.append(self._clean_product(row))
            except ValueError as e:
                raise ValueError(f"CSV line {i}: {e}") from e
        for r in rows:
            self.add_product(r)
        return len(rows)

    # ── sessions ──
    def start_session(self) -> int:
        return self._exec("INSERT INTO sessions(started_at) VALUES(?)", (time.time(),)).lastrowid  # type: ignore[return-value]

    def end_session(self, sid: int) -> None:
        self._exec("UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL", (time.time(), sid))

    def list_sessions(self, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM comments c WHERE c.session_id=s.id) AS comment_count, "
            "(SELECT COUNT(*) FROM replies r WHERE r.session_id=s.id AND r.status='spoken') AS spoken_count "
            "FROM sessions s ORDER BY s.id DESC LIMIT ?",
            (limit,),
        )

    # ── comments & replies ──
    def add_comment(self, session_id: int | None, user: str, text: str, source: str,
                    status: str, reason: str | None = None) -> int:
        return self._exec(
            "INSERT INTO comments(session_id, user, text, source, status, reason, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (session_id, user, text, source, status, reason, time.time()),
        ).lastrowid  # type: ignore[return-value]

    def set_comment_status(self, cid: int, status: str, reason: str | None = None) -> None:
        self._exec("UPDATE comments SET status=?, reason=COALESCE(?, reason) WHERE id=?", (status, reason, cid))

    def get_comment(self, cid: int) -> dict | None:
        return self._one("SELECT * FROM comments WHERE id=?", (cid,))

    def add_reply(self, session_id: int | None, comment_id: int | None, kind: str, text: str,
                  product_id: int | None, status: str, meta: dict | None = None) -> int:
        return self._exec(
            "INSERT INTO replies(session_id, comment_id, kind, text, product_id, status, meta, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, comment_id, kind, text, product_id, status, json.dumps(meta or {}), time.time()),
        ).lastrowid  # type: ignore[return-value]

    def set_reply_status(self, rid: int, status: str, text: str | None = None) -> None:
        spoken = time.time() if status == "spoken" else None
        self._exec(
            "UPDATE replies SET status=?, text=COALESCE(?, text), spoken_at=COALESCE(?, spoken_at) WHERE id=?",
            (status, text, spoken, rid),
        )

    def get_reply(self, rid: int) -> dict | None:
        r = self._one("SELECT * FROM replies WHERE id=?", (rid,))
        if r:
            r["meta"] = json.loads(r["meta"] or "{}")
        return r

    def history(self, session_id: int | None = None, limit: int = 200) -> dict:
        where, params = ("WHERE session_id=?", (session_id,)) if session_id else ("", ())
        comments = self._all(f"SELECT * FROM comments {where} ORDER BY id DESC LIMIT {int(limit)}", params)
        replies = self._all(f"SELECT * FROM replies {where} ORDER BY id DESC LIMIT {int(limit)}", params)
        for r in replies:
            r["meta"] = json.loads(r["meta"] or "{}")
        return {"comments": comments, "replies": replies}
