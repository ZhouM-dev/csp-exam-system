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
    import io
    import zipfile
    out = {}
    try:
      with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > MAX_UPLOAD_FILES:
            raise ValueError("压缩包文件数量超过上限。")
        if sum(i.file_size for i in infos) > max_bytes:
            raise ValueError("压缩包解压后的总大小超过上传上限。")
        for info in infos:
            if info.is_dir():
                continue
            if info.filename.startswith("__MACOSX") or "/." in info.filename:
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("压缩包不能包含符号链接。")
            if info.filename in out:
                raise ValueError("压缩包包含重复文件名。")
            if info.flag_bits & 1:
                raise ValueError("不能上传加密压缩包。")
            out[info.filename] = z.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
        raise ValueError("压缩包无法读取，请重新打包。") from e
    return out


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
