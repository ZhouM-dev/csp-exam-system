# -*- coding: utf-8 -*-
"""题目列表页「点开才发」的那两个接口能不能用 + 页面 JS 引用的 id 是否都存在。

为什么单独一个脚本：全路由冒烟只请求页面本身（`check` 连 403 都算通过），而
「查看题面」/「自己测试」都是**点开才发**的 POST，页面 200 完全不代表它们能用。

两个断言：

1. 查看题面：照**页面里那段 JS 的算法**把地址算出来再打一次。
   踩过：`scan_url` 以前是 `admin_url(key, path="/admin/scan")` 传进去的，那个值本身
   已经带 `?key=`，JS 后面又拼了一次 `window.CSP_KEY_QUERY`（也是 `?key=`），于是变成
   `/admin/scan?key=X?key=X` —— 服务端把 key 解析成 `X?key=X`，一律 403
   「管理密钥不正确」。登录 cookie 还有效时看不出来（`_check_admin` 会退回 cookie），
   服务一重启 cookie 失效就必现。

2. 页面脚本引用到的 id 是否都真的存在。
   踩过：`modal()` 渲染副标题时没给 id，而 JS 第一句就是
   `document.getElementById('pv-sub').textContent = …` → 抛 TypeError →
   **整个点击处理函数从第一行死掉**，「查看题面」「自己测试」点了毫无反应。
   两个 modal（pv/test）都缺，所以两个按钮一起坏；页面 200、元素也都在，肉眼完全看不出来。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
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


def main() -> int:
    key = open(os.path.join(R, "data", "admin_key.txt")).read().strip()
    with urllib.request.urlopen(f"{BASE}/admin/problems?key={urllib.parse.quote(key)}",
                                timeout=30) as r:
        html = r.read().decode("utf-8", "replace")

    # ---------------- 1. 查看题面 ----------------
    m = re.search(r"window\.CSP_KEY_QUERY = (\"(?:[^\"\\]|\\.)*\")", html)
    m2 = re.search(r"fetch\('([^']*)'\s*\+\s*\(window\.CSP_KEY_QUERY", html)
    if not m or not m2:
        check(False, "页面里找不到 CSP_KEY_QUERY 或「查看题面」的 fetch")
    else:
        key_query = json.loads(m.group(1))
        url = m2.group(1) + key_query
        n = url.count("key=")
        if n != 1:
            check(False, f"查看题面地址里的 key 出现 {n} 次（应 1 次）：{url}")
        else:
            cand = {}
            mc = re.search(r"window\.CSP_CAND = (\{.*?\});", html, re.S)
            if mc:
                cand = json.loads(mc.group(1))
            cached = [p for p in cand
                      if os.path.isfile(os.path.join(R, "data", "statements", f"{p}.md"))]
            pid = (cached or list(cand) or [""])[0]
            body = urllib.parse.urlencode({"render": "1", "pid": pid}).encode()
            req = urllib.request.Request(
                BASE + url if url.startswith("/") else url, data=body, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    got = json.loads(r.read().decode("utf-8", "replace"))
                if not got.get("ok"):
                    check(False, f"查看题面返回失败：{str(got.get('error'))[:140]}")
                elif cached and not (got.get("html") or "").strip():
                    check(False, f"题目 {pid} 有题面缓存，但返回的 HTML 是空的")
                else:
                    check(True, f"查看题面能取到题面（题目 {pid}，"
                                f"{len(got.get('html') or '')} 字节 HTML）")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:140]
                check(False, f"查看题面被拒（HTTP {e.code}）：{detail}")

    # ---------------- 2. 脚本引用的 id 都存在 ----------------
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    want = set(re.findall(r"\$\('([A-Za-z0-9_\-]+)'\)", html))
    want |= set(re.findall(r"getElementById\('([A-Za-z0-9_\-]+)'\)", html))
    missing = sorted(w for w in want if w not in ids)
    check(not missing,
          f"页面脚本引用的 id 都存在（{len(want)} 个）" if not missing
          else f"脚本引用但页面上没有的 id：{missing}（点击会抛 TypeError，按钮点了没反应）")

    # ---------------- 3. 题目表格不出现横向滚动条 ----------------
    # 表格是 7 列，长标题（尤其「用在本场：…」那截）会把表格顶宽、出现横向滚动条。
    # 现在靠 `.csp-prob-table` 的固定布局 + 按列限宽 + 省略号截断压住，全文留在 title 里。
    # 这里同时盯**标记**和**CSS 规则**：只查一边的话，另一边被改回去照样会溢出。
    has_class = 'class="csp-prob-table"' in html
    has_css = ".csp-prob-table { table-layout: fixed" in html
    check(has_class and has_css,
          "题目表格用固定布局（不会出现横向滚动条）" if (has_class and has_css)
          else f"题目表格的固定布局没上全：标记 {has_class}／CSS 规则 {has_css}"
               "（少一个就会被长标题顶出横向滚动条）")

    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
