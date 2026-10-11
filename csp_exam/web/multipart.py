"""multipart 表单解析用到的小工具：文件名解码、zip 展开。

拆出来单独放，是因为学生端（上传文件夹）和管理端（建题目/题单导入）都要用。
"""

from __future__ import annotations

import io
import os
import zipfile
import stat

from ..config import MAX_UPLOAD, MAX_UPLOAD_FILES


def _read_zip(data: bytes, *, max_bytes: int = MAX_UPLOAD) -> dict[str, bytes]:
    from ..core.archive import read_zip
    return read_zip(data, max_files=MAX_UPLOAD_FILES, max_bytes=max_bytes)


def _decode_name(raw: bytes) -> str:
    """把 multipart 里的文件名/字段名（原始字节）还原成字符串。

    浏览器发的是 UTF-8 字节；个别客户端可能发 GBK，兜一下。
    """
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _fix_filename(name: str) -> str:
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
