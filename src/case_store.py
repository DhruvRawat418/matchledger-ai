"""SQLite snapshots for a local, single-operator workflow."""
import json
import sqlite3
from pathlib import Path

class CaseConflict(ValueError):
    pass

class CaseStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def load(self, case_id):
        with self._connect() as db:
            row = db.execute("SELECT version, payload FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown case: {case_id}")
        return row[0], json.loads(row[1])

    def list_ids(self):
        """Case IDs, newest case first (for the local web app's case list)."""
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT case_id FROM cases ORDER BY rowid DESC")]

    def create(self, record):
        try:
            with self._connect() as db:
                db.execute("INSERT INTO cases VALUES (?, 1, ?)", (record['state']['case_id'], json.dumps(record)))
        except sqlite3.IntegrityError as exc:
            raise CaseConflict("Case already exists; use show or a new case ID") from exc
        return 1

    def save(self, record, expected_version):
        with self._connect() as db:
            changed = db.execute("UPDATE cases SET version = version + 1, payload = ? WHERE case_id = ? AND version = ?",
                (json.dumps(record), record['state']['case_id'], expected_version)).rowcount
            if changed != 1:
                raise CaseConflict("Saved case changed; reload before trying again")
        return expected_version + 1
