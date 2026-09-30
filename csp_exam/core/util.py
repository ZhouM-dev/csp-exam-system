"""基础工具：日志与格式化"""

from __future__ import annotations


import os
import time
from ..config import LOG_DIR    # noqa: E402  （配置集中在 config）


def _fmt_bytes(n) -> str:
    """文件大小：按字节输入，自己选单位。"""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "-"
    if v < 1024:
        return f"{v:.0f} B"
    if v < 1024 * 1024:
        return f"{v / 1024:.1f} KB"
    return f"{v / 1048576:.1f} MB"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(LOG_DIR, "exam.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _fmt_ms(v):
    try:
        return f"{float(v):.0f}ms"
    except (TypeError, ValueError):
        return "-"


def _fmt_kb(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{n / 1024:.1f}MB" if n >= 1024 else f"{n:.0f}KB"