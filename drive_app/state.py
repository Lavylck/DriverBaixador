"""Small, transactional SQLite store. No OAuth tokens are stored here."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import RemoteFile


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / "queue.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, destination TEXT NOT NULL,
                name TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, remote TEXT NOT NULL,
                name TEXT NOT NULL, relative_path TEXT NOT NULL, destination TEXT NOT NULL,
                size INTEGER, done INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                validator TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL, updated REAL NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS files_job ON files(job_id, state)")
            self.db.execute("CREATE INDEX IF NOT EXISTS files_dest ON files(destination, signature)")
            # Opening the program never starts an old queue without user intent.
            self.db.execute("UPDATE jobs SET state='cancelled', updated=? WHERE state='cancelling'", (time.time(),))
            self.db.execute("UPDATE jobs SET state='paused', updated=? WHERE state IN ('running','queued','planning','pausing')", (time.time(),))
            self.db.execute("UPDATE files SET state='paused', updated=? WHERE state IN ('running','queued','checking')", (time.time(),))
            self.db.execute("UPDATE files SET state='cancelled' WHERE job_id IN (SELECT id FROM jobs WHERE state='cancelled') AND state<>'completed'")

    def query(self, sql: str, values: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, values).fetchall()]

    def execute(self, sql: str, values: tuple = ()) -> None:
        with self.lock, self.db:
            self.db.execute(sql, values)

    def update(self, table: str, identity: str, **fields) -> None:
        allowed = {"jobs": {"name", "state", "error", "updated"},
                   "files": {"remote", "size", "done", "state", "error", "validator", "digest", "signature", "updated"}}
        if table not in allowed or not set(fields).issubset(allowed[table]):
            raise ValueError("Invalid state update")
        fields["updated"] = time.time()
        clause = ", ".join(name + "=?" for name in fields)
        self.execute(f"UPDATE {table} SET {clause} WHERE id=?", tuple(fields.values()) + (identity,))

    def add_job(self, identity: str, url: str, destination: str) -> None:
        now = time.time()
        self.execute("INSERT INTO jobs VALUES (?, ?, ?, 'Preparando download', 'planning', '', ?, ?)",
                     (identity, url, destination, now, now))

    def add_files(self, job_id: str, destination: Path, files: list[RemoteFile]) -> None:
        import uuid
        with self.lock, self.db:
            for remote in files:
                target = str(destination.joinpath(*remote.relative_path.split("/")))
                self.db.execute("""INSERT INTO files
                    (id,job_id,remote,name,relative_path,destination,size,state,signature,updated)
                    VALUES (?,?,?,?,?,?,?,'queued',?,?)""",
                    (uuid.uuid4().hex, job_id, json.dumps(remote.to_dict(), ensure_ascii=False),
                     remote.name, remote.relative_path, target, remote.size, remote.signature, time.time()))

    def file(self, identity: str) -> dict:
        return self.query("SELECT * FROM files WHERE id=?", (identity,))[0]

    def job(self, identity: str) -> dict:
        rows = self.query("SELECT * FROM jobs WHERE id=?", (identity,))
        if not rows:
            raise KeyError(identity)
        return rows[0]

    def known_digest(self, target: str, signature: str) -> str:
        rows = self.query("SELECT digest FROM files WHERE destination=? AND signature=? AND state='completed' AND digest<>'' ORDER BY updated DESC LIMIT 1",
                          (target, signature))
        return rows[0]["digest"] if rows else ""

    def close(self) -> None:
        with self.lock:
            self.db.close()
