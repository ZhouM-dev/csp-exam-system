"""学生会话与管理端 cookie 的签名（不改内容、只签名）"""

from __future__ import annotations


import hashlib
import hmac
import urllib.parse

from . import store
ADMIN_COOKIE = "csp_admin"


def _sign(text: str) -> str:
    key = store.admin_key().encode()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:24]


def make_cookie(cid: str, kaohao: str) -> str:
    """学生会话：绑定到**某一场考试**。

    考号是按场次随机分配的，同一个考号在别的考场可能属于别人，
    所以会话必须记住考场 id，不能只凭考号。
    """
    payload = f"{cid}:{kaohao}"
    return f"{urllib.parse.quote(payload, safe='')}.{_sign(payload)}"


def parse_cookie(raw: str) -> tuple[str, str] | None:
    """返回 (比赛 id, 考号)。"""
    if not raw or "." not in raw:
        return None
    payload, sig = raw.rsplit(".", 1)
    payload = urllib.parse.unquote(payload)
    if ":" not in payload:
        return None
    if hmac.compare_digest(sig, _sign(payload)):
        cid, _, kaohao = payload.partition(":")
        return (cid, kaohao) if cid and kaohao else None
    return None


def make_admin_cookie() -> str:
    return _sign("admin:" + store.admin_key())


def parse_admin_cookie(raw: str) -> bool:
    return bool(raw) and hmac.compare_digest(raw, make_admin_cookie())