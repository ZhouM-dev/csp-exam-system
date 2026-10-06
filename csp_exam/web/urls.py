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


def statement_url(key: str, pid: str = "", msg: str = "") -> str:
    """「改题面」页（`/admin/statement`）的地址：密钥 + 题库标识 + 提示语。

    **别在页面里手拼** `?key=…&pid=…&m=…`：第一个参数用 `?`、后面用 `&`，
    手拼很容易拼出两个问号（`admin_url` 那段注释记的就是这个坑）。
    """
    parts = []
    if key:
        parts.append("key=" + urllib.parse.quote(key))
    if pid:
        parts.append("pid=" + urllib.parse.quote(pid))
    if msg:
        parts.append("m=" + urllib.parse.quote(msg))
    return "/admin/statement" + (("?" + "&".join(parts)) if parts else "")


def problem_detail_url(key: str, pid: str = "", msg: str = "") -> str:
    """「题目详情」页（`/admin/problem-detail`）的地址：密钥 + 题库标识 + 提示语。

    这一页是**看题 + 改题**的整页视图（左边 Markdown 原文、右边学生看到的成品），
    列表页的「查看题面」和保存后的回跳都走它。和 `statement_url` 一样，
    别在页面里手拼查询串（两个问号的坑）。
    """
    parts = []
    if key:
        parts.append("key=" + urllib.parse.quote(key))
    if pid:
        parts.append("pid=" + urllib.parse.quote(pid))
    if msg:
        parts.append("m=" + urllib.parse.quote(msg))
    return "/admin/problem-detail" + (("?" + "&".join(parts)) if parts else "")


def admin_url(key: str, cid: str = "", msg: str = "", path: str = "/admin") -> str:
    """管理端跳转用的地址。踩过：直接 "/admin" + cid_query(...) + "?m=" 会拼出两个问号，
    结果 c 变成 "c2?m=..."，消息也丢了。"""
    qs = cid_query(key, cid)
    parts = [p for p in (qs[1:] if qs else "").split("&") if p]
    if msg:
        parts.append("m=" + urllib.parse.quote(msg))
    return path + (("?" + "&".join(parts)) if parts else "")
