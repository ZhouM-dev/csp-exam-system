"""Durable application jobs; scoring is delegated to the external judge."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
import time
from . import store

_DB_LOCK = threading.RLock()


@contextmanager
def connection():
    path = Path(store.DATA_DIR) / 'judge-queue.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        db = sqlite3.connect(path, timeout=30)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, ready REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)''')
            with db:
                yield db
        finally:
            db.close()


def put(cid, kaohao, picked, exam, sid):
    payload = json.dumps([cid, kaohao, picked, exam, sid], ensure_ascii=False)
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO jobs(id,payload,state,created,ready) VALUES(?,?,'queued',?,?)",
                   (f'{cid}/{kaohao}/{sid}', payload, time.time(), time.time()))


def recover():
    with connection() as db:
        db.execute("UPDATE jobs SET state='queued',ready=? WHERE state='running'", (time.time(),))


def claim():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT id,payload,attempts FROM jobs WHERE state='queued' AND ready<=? ORDER BY created LIMIT 1",
                         (time.time(),)).fetchone()
        if not row:
            return None
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (row[0],))
    payload = json.loads(row[1]); payload[2] = {int(k): v for k, v in payload[2].items()}
    return row[0], payload, row[2]


def complete(job_id, retry=False, attempts=0):
    with connection() as db:
        if retry:
            db.execute("UPDATE jobs SET state='queued',ready=?,attempts=attempts+1 WHERE id=?",
                       (time.time() + min(60, 5 * 2 ** min(attempts, 4)), job_id))
        else:
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))


def pending():
    with connection() as db:
        return db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
