import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import DomainError
from .manifest import canonical

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    raw BLOB NOT NULL,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS active (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    run_id TEXT NOT NULL REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS closing_sources (
    id TEXT PRIMARY KEY,
    count_raw BLOB NOT NULL,
    book_raw BLOB NOT NULL,
    count_sha256 TEXT NOT NULL,
    book_sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS closings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    source_id TEXT NOT NULL REFERENCES closing_sources(id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_closing (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    closing_id TEXT NOT NULL REFERENCES closings(id)
);
'''


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self):
        with self.connect() as db:
            row = db.execute('SELECT run_id FROM active WHERE slot = 1').fetchone()
        return row[0] if row else None

    def load(self, run_id=None):
        with self.connect() as db:
            if run_id is None:
                row = db.execute('SELECT payload FROM runs JOIN active ON runs.id = active.run_id').fetchone()
            else:
                row = db.execute('SELECT payload FROM runs WHERE id = ?', (run_id,)).fetchone()
        if row is None:
            raise DomainError('RUN_NOT_FOUND', 404)
        return json.loads(row[0])

    def sources(self, run_id):
        with self.connect() as db:
            return dict(db.execute('SELECT name, raw FROM sources WHERE run_id = ?', (run_id,)))

    def history(self):
        sql = '''SELECT runs.id, generated_at, json_extract(payload, '$.shift.name'),
                        json_extract(payload, '$.manifest.inputs.events.sha256'),
                        json_extract(payload, '$.dispatch.comparison.delta'), active.run_id IS NOT NULL
                 FROM runs LEFT JOIN active ON runs.id = active.run_id
                 ORDER BY generated_at DESC, runs.id'''
        keys = ('id', 'generated_at', 'shift', 'source_sha256', 'delta', 'active')
        with self.connect() as db:
            return [dict(zip(keys, row)) for row in db.execute(sql)]

    def publish(self, snapshot, sources, expected):
        manifest = snapshot['manifest']
        run_id = manifest['id']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT run_id FROM active WHERE slot = 1').fetchone()
            active = row[0] if row else None
            existing = db.execute('SELECT payload FROM runs WHERE id = ?', (run_id,)).fetchone()
            if existing is not None:
                stored = json.loads(existing[0])
                comparable = dict(snapshot, manifest=dict(manifest))
                for key in ('generated_at', 'elapsed_ms'):
                    comparable['manifest'][key] = stored['manifest'][key]
                if stored != comparable:
                    raise DomainError('REPLAY_DRIFT', 409)
                if active == run_id:
                    return False
            if active != expected:
                raise DomainError('STALE_RUN', 409, current=active)
            if existing is None:
                db.execute('INSERT INTO runs VALUES (?, ?, ?)',
                           (run_id, manifest['generated_at'], canonical(snapshot)))
                db.executemany('INSERT INTO sources VALUES (?, ?, ?)',
                               [(run_id, name, raw) for name, raw in sources.items()])
            if active is not None and active != run_id:
                self.mark_run_closings_superseded(db, active)
                db.execute('DELETE FROM active_closing WHERE slot = 1')
            db.execute('INSERT INTO active VALUES (1, ?) ON CONFLICT(slot) DO UPDATE SET run_id = excluded.run_id',
                       (run_id,))
            return existing is None

    def register_count_sheet(self, source_id, count_raw, book_raw, count_sha256, book_sha256, received_at):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # 来源身份同时覆盖点数单与车辆册：任一字节不同即为新来源；完全相同才幂等复用
            existing = db.execute('SELECT id FROM closing_sources WHERE id = ?',
                                  (source_id,)).fetchone()
            if existing is not None:
                return existing[0], False
            db.execute('INSERT INTO closing_sources VALUES (?, ?, ?, ?, ?, ?)',
                       (source_id, count_raw, book_raw, count_sha256, book_sha256, received_at))
            return source_id, True

    def count_sheet(self, source_id):
        with self.connect() as db:
            row = db.execute(
                'SELECT count_raw, book_raw, count_sha256, book_sha256, received_at FROM closing_sources WHERE id = ?',
                (source_id,)).fetchone()
        if row is None:
            raise DomainError('COUNT_SHEET_NOT_FOUND', 404)
        keys = ('count_raw', 'book_raw', 'count_sha256', 'book_sha256', 'received_at')
        result = dict(zip(keys, row))
        result['count_raw'] = bytes(result['count_raw'])
        result['book_raw'] = bytes(result['book_raw'])
        return result

    def closing_state(self):
        with self.connect() as db:
            row = db.execute('''SELECT c.id, c.run_id, c.status, c.created_at, c.payload
                                FROM active_closing a JOIN closings c ON c.id = a.closing_id''').fetchone()
            if row is None:
                return None
            return {'id': row[0], 'run_id': row[1], 'status': row[2], 'created_at': row[3],
                    'payload': json.loads(row[4])}

    def closing(self, closing_id=None):
        with self.connect() as db:
            if closing_id is None:
                row = db.execute('''SELECT c.payload FROM active_closing a JOIN closings c ON c.id = a.closing_id''').fetchone()
            else:
                row = db.execute('SELECT payload FROM closings WHERE id = ?', (closing_id,)).fetchone()
        if row is None:
            raise DomainError('CLOSING_NOT_FOUND', 404)
        return json.loads(row[0])

    def closing_history(self):
        sql = '''SELECT c.id, c.run_id, c.created_at, c.status,
                        json_extract(c.payload, '$.opening_base.fleet_base'),
                        json_extract(c.payload, '$.opening_base.suspense_held'),
                        s.count_sha256, a.closing_id IS NOT NULL
                 FROM closings c JOIN closing_sources s ON s.id = c.source_id
                 LEFT JOIN active_closing a ON a.closing_id = c.id
                 ORDER BY c.created_at DESC, c.id'''
        keys = ('id', 'run_id', 'created_at', 'status', 'fleet_base', 'suspense_held',
                'count_sha256', 'active')
        with self.connect() as db:
            return [dict(zip(keys, row)) for row in db.execute(sql)]

    def mark_run_closings_superseded(self, db, run_id):
        db.execute("UPDATE closings SET status = 'superseded' WHERE run_id = ? AND status = 'active'",
                   (run_id,))

    def save_closing(self, snapshot, expected_run, expected_closing):
        closing_id = snapshot['manifest']['id']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            active_run = db.execute('SELECT run_id FROM active WHERE slot = 1').fetchone()
            active_run = active_run[0] if active_run else None
            if active_run != expected_run:
                raise DomainError('STALE_RUN', 409, current=active_run)
            active_closing_row = db.execute('SELECT closing_id FROM active_closing WHERE slot = 1').fetchone()
            active_closing = active_closing_row[0] if active_closing_row else None
            # 同一点数单重算得到同一结论：幂等返回，不重复调整，也不产生新版本
            stored = db.execute('SELECT id FROM closings WHERE id = ?', (closing_id,)).fetchone()
            if stored is not None:
                return False
            if active_run != expected_run:
                raise DomainError('STALE_RUN', 409, current=active_run)
            if active_closing != expected_closing:
                raise DomainError('STALE_CLOSING', 409, current=active_closing)
            if active_closing is not None:
                db.execute("UPDATE closings SET status = 'superseded' WHERE id = ?", (active_closing,))
            db.execute('INSERT INTO closings VALUES (?, ?, ?, ?, ?, ?)',
                       (closing_id, snapshot['run_id'], snapshot['source_id'],
                        snapshot['manifest']['generated_at'], 'active', canonical(snapshot)))
            db.execute('INSERT INTO active_closing VALUES (1, ?) ON CONFLICT(slot) DO UPDATE SET closing_id = excluded.closing_id',
                       (closing_id,))
            return True
