# -*- coding: utf-8 -*-
"""「题目详情」这条路通不通（只读，一个字都不改）。

盯三件事：

1. **题目列表每行要有入口** —— 老师是在「题目列表」找到那道题再点进去的，
   入口没挂上去，功能就等于不存在（和「查看题面」当初缺 id 是同一类问题）。
   这一版列表页的「查看题面」不再弹窗，而是跳到**题目详情页**。
2. **详情页要能打开，并且左右两栏、三个视图按钮、保存按钮都在** —— 这一页是
   「左边改（Markdown 原文）/ 右边看（学生视角成品）」的整页视图，缺一块就等于
   退回老样子（弹窗看一眼睛、另开一页改）。
3. **左边编辑框要预填当前题面** —— 起点是空框的话，老师一保存就把原来的题面
   清空了（比没有这个功能还糟）。

真改题面的验证在端到端里（`_e2e_mkproblem.sh` 改的是它自己建的那道测试题，
改完还会连着学生端题面页一起核对），这里只查读的路径。

为什么单独一个脚本：冒烟只请求页面本身（`check` 连 403 都算通过），
而这三件事都要看页面**内容**，`/admin/problems` 返回 200 完全不代表入口在。
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
import urllib.request

R = "/root/csp-exam"
BASE = "http://127.0.0.1:8080"

BAD = 0


def check(cond: bool, msg: str) -> None:
    global BAD
    print(("   [PASS] " if cond else "   [FAIL] ") + msg, flush=True)
    if not cond:
        BAD += 1


def page(url: str, timeout: int = 30) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main() -> int:
    key = open(os.path.join(R, "data", "admin_key.txt")).read().strip()
    kq = urllib.parse.quote(key)

    # ---------------- 1. 题目列表每行都有进详情页的入口 ----------------
    pl = page(f"{BASE}/admin/problems?key={kq}")
    rows = len(re.findall(r'data-test="', pl))            # 在用的题（已删除的行没有「自己测试」）
    views = len(re.findall(r'href="/admin/problem-detail\?', pl))
    check(views > 0, f"题目列表里有「查看题面」入口（{views} 个）")
    # 每一道在用的题都要有入口；「已删除的题目」那一块（可能一道都没有）另有若干
    check(views >= rows,
          f"每个能看题面的行都有入口（列表 {rows} 行 / 入口 {views} 个）")
    check("data-view=" not in pl,
          "老的弹窗式「查看题面」已经撤掉（不再弹窗，直接进详情页）")

    # ---------------- 2. 详情页：能打开 + 左右两栏 + 视图按钮 ----------------
    pids = re.findall(r'href="/admin/problem-detail\?[^"]*?pid=([A-Za-z0-9_\-]+)', pl)
    if not pids:
        check(False, "题目列表里一道题都没有，没法测详情页")
        return 1 if BAD else 0
    pid = pids[0]
    url = f"{BASE}/admin/problem-detail?key={kq}&pid={urllib.parse.quote(pid)}"
    try:
        html = page(url, timeout=60)
    except Exception as e:                                   # noqa: BLE001
        check(False, f"题目详情页打不开（{pid}）：{e}")
        return 1 if BAD else 0
    check('name="statement"' in html and 'id="pd-src"' in html,
          f"详情页有题面编辑框（题目 {pid}）")
    check("保存修改" in html, "详情页有保存按钮")
    check('class="pd-left"' in html and 'class="pd-right"' in html and 'id="pd-render"' in html,
          "详情页是左右两栏（左边编辑 / 右边渲染）")
    check(html.count("data-pd-mode=") == 3,
          "三个视图按钮都在（左右对照 / 只看编辑 / 只看成品）")
    check('name="title"' in html and 'name="name"' in html,
          "题目名 / 英文名都能在左边直接改")
    check('name="time_ms"' in html and 'name="memory_mb"' in html,
          "时限 / 内存也能在左边改")
    check("标程" in html, "详情页能看标程")
    check(f'name="pid" value="{pid}"' in html,
          "表单带着题目标识（保存时知道改哪道，不会改错题）")
    check(f"pid={urllib.parse.quote(pid)}" in html,
          "预览/保存的地址里带着这道题的标识")

    # ---------------- 3. 编辑框预填当前题面 ----------------
    cache = os.path.join(R, "data", "statements", f"{pid}.md")
    if os.path.isfile(cache):
        text = open(cache, encoding="utf-8").read().strip()
        head = text[:30].replace("\r", "")
        check(bool(head) and head in html,
              "左边编辑框预填了当前题面（对得上缓存里那份的开头）")
    else:
        print(f"   （{pid} 本地没有题面缓存，跳过预填检查）")

    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
