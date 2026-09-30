"""管理端链接的拼装（密钥/比赛/考号 → 查询串）。

单独放一个模块：路由、管理端页面、考号表都要拼这种链接，放谁家都不合适。
"""

from __future__ import annotations

import urllib.parse


def cid_query(key: str, cid: str, kaohao: str = "") -> str:
    """拼查询串：管理端链接统一走这个（密钥可空——cookie 也能认证）。"""
    parts = []
    if key:
        parts.append("key=" + urllib.parse.quote(key))
    if cid:
        parts.append("c=" + urllib.parse.quote(cid))
    if kaohao:
        parts.append("k=" + urllib.parse.quote(kaohao))
    return ("?" + "&".join(parts)) if parts else ""


def admin_url(key: str, cid: str = "", msg: str = "", path: str = "/admin") -> str:
    """管理端跳转用的地址。踩过：直接 "/admin" + cid_query(...) + "?m=" 会拼出两个问号，
    结果 c 变成 "c2?m=..."，消息也丢了。"""
    qs = cid_query(key, cid)
    parts = [p for p in (qs[1:] if qs else "").split("&") if p]
    if msg:
        parts.append("m=" + urllib.parse.quote(msg))
    return path + (("?" + "&".join(parts)) if parts else "")
