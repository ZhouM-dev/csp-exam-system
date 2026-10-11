"""Consistent offline backup. Refuses to run while the CSP application owns the data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core.runtime_lock import data_lock


def backup(directory, output):
    root, output = Path(directory).resolve(), Path(output).resolve()
    if not root.is_dir() or not ((root / "contests.json").is_file() or (root / "csp-exam.sqlite3").is_file()):
        raise ValueError("目录不是已初始化的 CSP 比赛数据目录。")
    if output.is_relative_to(root):
        raise ValueError("备份必须保存在数据目录之外。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with data_lock(root):
        # The service lock ensures no writer can restart during checkpoint/copy.
        for path in root.glob("*.sqlite3"):
            db = sqlite3.connect(path, timeout=30)
            try:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError(f"数据库完整性检查失败：{path.name}")
                if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise ValueError(f"数据库仍在使用：{path.name}")
            finally:
                db.close()
        files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.name != ".service.lock"]
        if any(p.is_symlink() for p in root.rglob("*")):
            raise ValueError("数据目录包含符号链接，请先检查。")
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as tar:
                for p in files: tar.add(p, arcname=p.relative_to(root).as_posix())
        except Exception:
            output.unlink(missing_ok=True)
            raise
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return {"file": str(output), "files": len(files), "sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(backup(args.data, args.output), ensure_ascii=False))


if __name__ == "__main__": main()
