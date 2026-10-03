# -*- coding: utf-8 -*-
"""「改题面」这条路通不通（只读，一个字都不改）。

盯两件事：

1. **题目列表每行要有入口** —— 老师是在「题目列表」找到那道题再改的，
   入口没挂上去，功能就等于不存在（和「查看题面」当初缺 id 是同一类问题）。
2. **编辑页要能打开、并且预填了当前题面** —— 起点是空框的话，老师一保存就把
   原来的题面清空了（比没有这个功能还糟）。

真改题面的验证在端到端里（`_e2e_mkproblem.sh` 改的是它自己建的那道测试题，
改完还会连着学生端题面页一起核对），这里只查读的路径。

为什么单独一个脚本：冒烟只请求页面本身（`check` 连 403 都算通过），
而这两件事都要看页面**内容**，`/admin/problems` 返回 200 完全不代表入口在。
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

    # ---------------- 1. 题目列表每行都有「改题面」入口 ----------------
    pl = page(f"{BASE}/admin/problems?key={kq}")
    views = len(re.findall(r'data-view="', pl))
    edits = len(re.findall(r'href="/admin/statement\?', pl))
    check(edits > 0, f"题目列表里有「改题面」入口（{edits} 个）")
    check(edits >= views,
          f"每个能看题面的行都有「改题面」（查看 {views} 个 / 改题面 {edits} 个）")

    # ---------------- 2. 编辑页：能打开 + 预填当前题面 ----------------
    pids = re.findall(r'data-view="([^"]+)"', pl)
    if not pids:
        check(False, "题目列表里一道题都没有，没法测编辑页")
        return 1 if BAD else 0
    pid = pids[0]
    url = f"{BASE}/admin/statement?key={kq}&pid={urllib.parse.quote(pid)}"
    try:
        html = page(url, timeout=60)
    except Exception as e:                                   # noqa: BLE001
        check(False, f"改题面页打不开（{pid}）：{e}")
        return 1 if BAD else 0
    check("name=\"statement\"" in html and "id=\"stmt-src\"" in html,
          f"编辑页有题面编辑框（题目 {pid}）")
    check("保存题面" in html, "编辑页有保存按钮")
    check(f'name="pid" value="{pid}"' in html,
          "表单带着题目标识（保存时知道改哪道，不会改错题）")
    check(f"pid={urllib.parse.quote(pid)}" in html,
          "预览/保存的地址里带着这道题的标识")

    # 预填：本地缓存里有这道题的题面时，编辑框必须拿它当起点
    cache = os.path.join(R, "data", "statements", f"{pid}.md")
    if os.path.isfile(cache):
        text = open(cache, encoding="utf-8").read().strip()
        head = text[:30].replace("\r", "")
        check(head and head in html,
              f"编辑框预填了当前题面（对得上缓存里那份的开头）")
    else:
        print(f"   （{pid} 本地没有题面缓存，跳过预填检查）")

    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
