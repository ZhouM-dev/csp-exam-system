"""不可变交卷目录、路径校验与归档配额。"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import uuid

from ..config import MAX_SOURCE_BYTES, MAX_STUDENT_ARCHIVE, MAX_UPLOAD, MAX_UPLOAD_FILES
from . import store


def clean_files(files: dict[str, bytes]) -> dict[str, bytes]:
    if len(files) > MAX_UPLOAD_FILES:
        raise ValueError("上传文件数量超过上限。")
    if sum(len(v) for v in files.values()) > MAX_UPLOAD:
        raise ValueError("全部文件解压后的总大小超过 32 MiB。")
    result = {}
    for raw, data in files.items():
        name = str(raw).replace("\\", "/")
        parts = name.split("/")
        if (not name or "\0" in name or name.startswith("/")
                or any(p in ("", ".", "..") or ":" in p for p in parts)):
            raise ValueError("上传包含不安全的文件路径。")
        name = str(PurePosixPath(name))
        if name in result:
            raise ValueError("上传包含重复文件路径。")
        if Path(name).suffix.lower() in (".cpp", ".cc", ".cxx", ".c++", ".c", ".pas"):
            if len(data) > MAX_SOURCE_BYTES:
                raise ValueError(f"源文件 {name} 超过 100 KiB。")
        result[name] = data
    return result


def confined_file(base: str, rel: str) -> str:
    name = str(rel or "").replace("\\", "/")
    if not name or name.startswith("/") or any(p in ("", ".", "..") or ":" in p for p in name.split("/")):
        raise ValueError("文件路径不安全。")
    root = Path(base).resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise ValueError("文件路径越过了提交目录。") from e
    return str(target)


def archive(cid: str, kaohao: str, files: dict[str, bytes]) -> tuple[str, str]:
    """返回 (提交 ID, 相对于 upload_dir 的快照目录)。调用方持有 store._LOCK。"""
    base = Path(store.upload_dir(cid, kaohao))
    base.mkdir(parents=True, exist_ok=True)
    existing = sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
    size = sum(map(len, files.values()))
    if existing + size > MAX_STUDENT_ARCHIVE:
        raise ValueError("提交归档超过 256 MiB，请联系老师导出并清理归档。")
    if shutil.disk_usage(base).free < size + 512 * 1024 * 1024:
        raise ValueError("服务器磁盘空间不足，请联系老师处理。")
    sid = uuid.uuid4().hex
    rel = f"__submissions/{sid}"
    directory = base / rel
    directory.mkdir(parents=True)
    try:
        for name, data in files.items():
            target = Path(confined_file(str(directory), name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except Exception:
        shutil.rmtree(directory)
        raise
    return sid, rel
