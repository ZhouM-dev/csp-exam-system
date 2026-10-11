"""SQLite business storage. Binary judge inputs and uploaded source stay on disk.

Collections are stored as individually keyed rows; nested scoring records retain
their JSON representation. Logical document names keep the application API stable,
but are not files. Once initialized, SQLite is the sole authority.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

FILENAME = "csp-exam.sqlite3"
ROOT_DOCUMENTS = {"groups.json", "students.json", "contests.json", "problem_codes.json",
                  "problem_info.json", "problem_catalog.json", "judge_profile.json",
                  "exam.json", "roster.json", "results.json"}
_LOCK = threading.RLock()
_LOCAL = threading.local()


def key_for(root, path):
    try:
        key = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None
    if key in ROOT_DOCUMENTS or re.fullmatch(r"contests/[^/]+/(exam|roster|results)\.json", key):
        return key
    if re.fullmatch(r"samples/[^/]+/manifest\.json", key):
        return key
    return None


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _put(db, key, data):
    shape, header, value = "scalar", None, data
    if isinstance(data, dict) and isinstance(data.get("items"), (list, dict)):
        shape, header, value = "wrapped", {k: v for k, v in data.items() if k != "items"}, data["items"]
    kind = "list" if isinstance(value, list) else "dict" if isinstance(value, dict) else "scalar"
    shape = kind if shape != "wrapped" else "wrapped-" + kind
    db.execute("INSERT INTO documents(key,shape,header,updated_at) VALUES(?,?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET shape=excluded.shape,header=excluded.header,updated_at=excluded.updated_at,revision=documents.revision+1",
               (key, shape, _encode(header), time.time()))
    db.execute("DELETE FROM records WHERE document_key=?", (key,))
    pairs = enumerate(value) if kind == "list" else value.items() if kind == "dict" else [("value", value)]
    db.executemany("INSERT INTO records(document_key,item_key,position,payload) VALUES(?,?,?,?)",
                   ((key, str(k), i, _encode(v)) for i, (k, v) in enumerate(pairs)))


def _get(db, key, default):
    document = db.execute("SELECT shape,header FROM documents WHERE key=?", (key,)).fetchone()
    if document is None:
        return default() if callable(default) else default
    shape, header = document
    rows = db.execute("SELECT item_key,payload FROM records WHERE document_key=? ORDER BY position", (key,)).fetchall()
    kind = shape.removeprefix("wrapped-")
    value = [json.loads(v) for _, v in rows] if kind == "list" else {k: json.loads(v) for k, v in rows} if kind == "dict" else json.loads(rows[0][1])
    return {**json.loads(header), "items": value} if shape.startswith("wrapped-") else value


def legacy_documents(root):
    root = Path(root)
    candidates = [root / name for name in ROOT_DOCUMENTS]
    candidates += list((root / "contests").glob("*/exam.json")) + list((root / "contests").glob("*/roster.json")) + list((root / "contests").glob("*/results.json"))
    candidates += list((root / "samples").glob("*/manifest.json"))
    return sorted(p for p in candidates if p.is_file())


def _initialize(db, root):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 1:
        return
    if version != 0:
        raise OSError(f"不支持的题库数据库版本：{version}")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      BEGIN IMMEDIATE;
      CREATE TABLE documents(key TEXT PRIMARY KEY, shape TEXT NOT NULL, header TEXT NOT NULL CHECK(json_valid(header)), updated_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE records(document_key TEXT NOT NULL REFERENCES documents(key) ON DELETE CASCADE, item_key TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)), PRIMARY KEY(document_key,item_key));
      CREATE TABLE statements(pid TEXT PRIMARY KEY, markdown TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
      CREATE VIEW problem_library AS
        SELECT r.item_key AS id, json_extract(r.payload,'$.title') AS title,
          coalesce(s.markdown,'') AS description,
          coalesce(json_extract(r.payload,'$.time_ms'),1000) AS time_limit,
          coalesce(json_extract(r.payload,'$.memory_mb'),256) AS memory_limit,
          json_extract(r.payload,'$.name') AS submission_name,
          json_extract(r.payload,'$.code') AS problem_number,
          coalesce(json_extract(r.payload,'$.deleted'),0) AS deleted
        FROM records r LEFT JOIN statements s ON s.pid=r.item_key
        WHERE r.document_key='problem_info.json';
    """)
    try:
        for path in legacy_documents(root):
            _put(db, key_for(root, path), json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((Path(root) / "statements").glob("*.md")):
            db.execute("INSERT INTO statements(pid,markdown,updated_at) VALUES(?,?,?)",
                       (path.stem, path.read_text(encoding="utf-8"), time.time()))
        db.execute("PRAGMA user_version=1")
        db.commit()
    except Exception:
        db.rollback()
        raise


@contextmanager
def connection(root):
    root = Path(root).resolve()
    active = getattr(_LOCAL, "active", None)
    if active and active[0] == root:
        yield active[1]
        return
    with _LOCK:
        root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(root / FILENAME, timeout=30)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            _initialize(db, root)
            yield db
            db.commit()
        except sqlite3.Error as error:
            db.rollback()
            raise OSError(f"SQLite 数据存储失败：{error}") from error
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@contextmanager
def transaction(root):
    with connection(root) as db:
        old = getattr(_LOCAL, "active", None)
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        _LOCAL.active = (Path(root).resolve(), db)
        try:
            yield
        finally:
            _LOCAL.active = old


def load(root, key, default):
    with connection(root) as db:
        return _get(db, key, default)


def save(root, key, data):
    with connection(root) as db:
        _put(db, key, data)


def exists(root, key):
    with connection(root) as db:
        return db.execute("SELECT 1 FROM documents WHERE key=?", (key,)).fetchone() is not None


def delete(root, prefix):
    with connection(root) as db:
        # Prefix is literal, never SQL LIKE (which treats '_' and '%' specially).
        db.execute("DELETE FROM documents WHERE key=? OR substr(key,1,?)=?", (prefix, len(prefix + "/"), prefix + "/"))


def statement(root, pid):
    with connection(root) as db:
        row = db.execute("SELECT markdown FROM statements WHERE pid=?", (pid,)).fetchone()
        return row[0] if row else ""


def save_statement(root, pid, text):
    with connection(root) as db:
        db.execute("INSERT INTO statements(pid,markdown,updated_at) VALUES(?,?,?) "
                   "ON CONFLICT(pid) DO UPDATE SET markdown=excluded.markdown, revision=statements.revision+1,updated_at=excluded.updated_at",
                   (pid, text.replace("\r\n", "\n").replace("\r", "\n"), time.time()))


def delete_statement(root, pid):
    with connection(root) as db:
        return db.execute("DELETE FROM statements WHERE pid=?", (pid,)).rowcount


def signature(root, keys):
    with connection(root) as db:
        return tuple((key, db.execute("SELECT revision FROM documents WHERE key=?", (key,)).fetchone()) for key in keys)
