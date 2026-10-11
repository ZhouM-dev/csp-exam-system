"""One bounded, lossless ZIP reader for student and teacher uploads."""
import io
import stat
import struct
import unicodedata
import zipfile
import zlib


def safe_name(name):
    name = unicodedata.normalize("NFC", name.replace("\\", "/"))
    if (not name or name.startswith("/") or ":" in name
            or any(p in ("", ".", "..") for p in name.split("/"))
            or any(ord(c) < 32 for c in name)):
        raise ValueError("压缩包或上传包含无效、越界文件名。")
    return name


def member_name(info):
    if "\x00" in info.orig_filename:
        raise ValueError("压缩包包含无效文件名。")
    name = info.filename
    if not info.flag_bits & 0x800:
        raw = name.encode("cp437")
        extra = info.extra
        while len(extra) >= 4:
            kind, size = struct.unpack("<HH", extra[:4])
            payload, extra = extra[4:4 + size], extra[4 + size:]
            if kind == 0x7075 and len(payload) >= 5 and payload[0] == 1:
                if struct.unpack("<I", payload[1:5])[0] == zlib.crc32(raw):
                    try:
                        return payload[5:].decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError("压缩包的 Unicode 文件名损坏。")
        # Windows legacy ZIPs contain CP936 bytes without the UTF-8 flag.
        if any(b >= 128 for b in raw):
            try:
                decoded = raw.decode("gb18030")
                if any("\u3400" <= c <= "\u9fff" for c in decoded):
                    name = decoded
            except UnicodeDecodeError:
                pass
    return name


def members(zf, *, max_files, max_bytes):
    out, total = {}, 0
    infos = zf.infolist()
    if len(infos) > max_files:
        raise ValueError("压缩包文件数量超过上限。")
    for info in infos:
        name = member_name(info).replace("\\", "/")
        if info.is_dir() or name.endswith("/"):
            continue
        name = safe_name(name)
        if "__MACOSX" in name.split("/") or name.rsplit("/", 1)[-1] == ".DS_Store":
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError("压缩包不能包含符号链接。")
        if info.flag_bits & 1:
            raise ValueError("不能上传加密压缩包。")
        if name in out:
            raise ValueError("压缩包包含重复文件名。")
        total += info.file_size
        if total > max_bytes:
            raise ValueError("压缩包解压后的总大小超过上传上限。")
        out[name] = info
    return out


def read_zip(data, *, max_files=20000, max_bytes=512 * 1024 * 1024):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            index = members(zf, max_files=max_files, max_bytes=max_bytes)
            return {name: zf.read(info) for name, info in index.items()}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, UnicodeError) as e:
        raise ValueError("压缩包无法读取，请重新打包。") from e
