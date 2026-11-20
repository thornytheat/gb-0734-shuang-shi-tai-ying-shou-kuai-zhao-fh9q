import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS archives (
  run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
  rule_version TEXT NOT NULL, generated_at TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
  result_digest TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS archives_time ON archives(generated_at DESC, run_id);
CREATE TABLE IF NOT EXISTS restatements (
  restate_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL,
  rule_version TEXT NOT NULL, generated_at TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
  result_digest TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS restatements_time ON restatements(generated_at DESC, restate_id);
CREATE TABLE IF NOT EXISTS restatement_sources (
  restate_id TEXT NOT NULL REFERENCES restatements(restate_id),
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  seq INTEGER NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (restate_id, seq)
);
CREATE INDEX IF NOT EXISTS restatement_sources_source ON restatement_sources(source_id);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def get_store(request: Request) -> Store:
    return request.app.state.store
