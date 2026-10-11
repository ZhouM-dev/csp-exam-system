"""Offline, verified migration from legacy JSON/Markdown to SQLite."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import database
from csp_exam.core.runtime_lock import data_lock
from csp_exam.tools.backup_data import backup


def migrate(root, backup_path=None, archive=None):
    root = Path(root).resolve()
    if backup_path:
        backup(root, backup_path)
    with data_lock(root):
        paths = database.legacy_documents(root)
        expected = {database.key_for(root, p): json.loads(p.read_text(encoding="utf-8")) for p in paths}
        statements = {p.stem: p.read_text(encoding="utf-8") for p in (root / "statements").glob("*.md")}
        existed = (root / database.FILENAME).exists()
        with database.connection(root) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite 完整性检查失败。")
            for key, value in expected.items():
                if database._get(db, key, lambda: None) != value:
                    raise ValueError(f"迁移核对不一致：{key}；请勿覆盖已有数据库。")
            for pid, text in statements.items():
                if database.statement(root, pid) != text:
                    raise ValueError(f"题面迁移核对不一致：{pid}")
            report = {"database": str(root / database.FILENAME), "already_initialized": existed,
                      "verified_documents": len(expected), "verified_statements": len(statements),
                      "records": db.execute("SELECT count(*) FROM records").fetchone()[0],
                      "integrity": "ok"}
        if archive:
            archive = Path(archive).resolve()
            if archive.is_relative_to(root) or archive.exists():
                raise ValueError("旧格式归档须为数据目录之外的新目录。")
            archive.mkdir(parents=True, mode=0o700)
            for path in paths + list((root / "statements").glob("*.md")):
                target = archive / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                path.rename(target)
            report["archived_files"] = len(paths) + len(statements)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.data, args.backup, args.archive), ensure_ascii=False))


if __name__ == "__main__": main()
