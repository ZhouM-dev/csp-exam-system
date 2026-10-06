# -*- coding: utf-8 -*-
"""题目列表 / 题目详情两页里「点开才发」的接口能不能用 + 页面 JS 引用的 id 是否都存在。

为什么单独一个脚本：全路由冒烟只请求页面本身（`check` 连 403 都算通过），而
「查看题面」的渲染、「自己测试」都是**点开才发**的 POST，页面 200 完全不代表它们能用。

四个断言：

1. 题目详情页的实时渲染：照**详情页里那段 JS 的算法**把地址算出来再打一次。
   踩过：`scan_url` 以前是 `admin_url(key, path="/admin/scan")` 传进去的，那个值本身
   已经带 `?key=`，JS 后面又拼了一次 `window.CSP_KEY_QUERY`（也是 `?key=`），于是变成
   `/admin/scan?key=X?key=X` —— 服务端把 key 解析成 `X?key=X`，一律 403
   「管理密钥不正确」。登录 cookie 还有效时看不出来（`_check_admin` 会退回 cookie），
   服务一重启 cookie 失效就必现。

2. 两页脚本引用到的 id 是否都真的存在。
   踩过：`modal()` 渲染副标题时没给 id，而 JS 第一句就是
   `document.getElementById('pv-sub').textContent = …` → 抛 TypeError →
   **整个点击处理函数从第一行死掉**，按钮点了毫无反应。页面 200、元素也都在，肉眼看不出来。

3. 操作列排成一行（`.prob-ops` 的 flex + nowrap）：四个按钮挤在两行时，行高忽高忽低，
   删除按钮还容易被漏看 —— 这是老师点名要改的样子，写进验收里免得被改回去。

4. 删除走 AJAX（不整页跳转）：页面里必须有 `json=1` + `prob-rows` 这两样，
   否则就是退回"form POST + 302 回列表页"的老路 —— 点完删除会被弹回页面顶部。
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


def js_obj(html: str, name: str):
    """取页面里 `window.<name> = {...};` 那份 JSON —— **按花括号配对取**。

    别用 `re.search(r"\\{.*?\\};")` 那种懒惰正则：页面里嵌的不只是标题/编号，
    还有**标程源码**（「自己测试」要用它预填代码框），源码里随便一句
    `int cnt[26] = {0};` 就带着 `};` —— 正则停在那儿，JSON 被截断，
    整个检查脚本直接抛 JSONDecodeError 退出（踩过：冒烟里这一项静悄悄地不作声，
    33 项通过里少了它的 3 项，看汇总还以为是全绿）。

    字符串里的花括号和转义引号都要跳过，所以手写一遍扫描。
    """
    head = f"window.{name} = "
    i = html.find(head)
    if i < 0:
        return None
    i += len(head)
    depth = 0
    in_str = False
    esc = False
    for k in range(i, len(html)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i:k + 1])
                except ValueError:
                    return None
    return None


def ids_ok(html: str, where: str) -> set:
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    want = set(re.findall(r"\$\('([A-Za-z0-9_\-]+)'\)", html))
    want |= set(re.findall(r"getElementById\('([A-Za-z0-9_\-]+)'\)", html))
    missing = sorted(w for w in want if w not in ids)
    check(not missing,
          f"{where}脚本引用的 id 都存在（{len(want)} 个）" if not missing
          else f"{where}脚本引用但页面上没有的 id：{missing}"
               "（点击会抛 TypeError，按钮点了没反应）")
    return ids


def main() -> int:
    key = open(os.path.join(R, "data", "admin_key.txt")).read().strip()
    kq = urllib.parse.quote(key)
    with urllib.request.urlopen(f"{BASE}/admin/problems?key={kq}", timeout=30) as r:
        html = r.read().decode("utf-8", "replace")

    # ---------------- 1. 详情页的实时渲染 ----------------
    pids = re.findall(r'href="/admin/problem-detail\?[^"]*?pid=([A-Za-z0-9_\-]+)', html)
    pid = (pids or [""])[0]
    if not pid:
        check(False, "题目列表里没有进详情页的入口，没法测实时渲染")
        detail = ""
    else:
        with urllib.request.urlopen(
                f"{BASE}/admin/problem-detail?key={kq}&pid={urllib.parse.quote(pid)}",
                timeout=60) as r:
            detail = r.read().decode("utf-8", "replace")
        m = re.search(r"window\.CSP_KEY_QUERY = (\"(?:[^\"\\]|\\.)*\")", detail)
        m2 = re.search(r"fetch\('([^']*)'\s*\+\s*\(window\.CSP_KEY_QUERY", detail)
        if not m or not m2:
            check(False, "详情页里找不到 CSP_KEY_QUERY 或实时渲染的 fetch")
        else:
            url = m2.group(1) + json.loads(m.group(1))
            n = url.count("key=")
            if n != 1:
                check(False, f"渲染地址里的 key 出现 {n} 次（应 1 次）：{url}")
            else:
                cache = os.path.join(R, "data", "statements", f"{pid}.md")
                text = ""
                if os.path.isfile(cache):
                    text = open(cache, encoding="utf-8").read()
                body = urllib.parse.urlencode({"render": "1", "statement": text}).encode()
                req = urllib.request.Request(
                    BASE + url if url.startswith("/") else url, data=body, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        got = json.loads(r.read().decode("utf-8", "replace"))
                    if not got.get("ok"):
                        check(False, f"详情页渲染返回失败：{str(got.get('error'))[:140]}")
                    elif text.strip() and not (got.get("html") or "").strip():
                        check(False, f"题目 {pid} 有题面，但渲染出来的 HTML 是空的")
                    else:
                        check(True, f"详情页实时渲染能用（题目 {pid}，"
                                    f"{len(got.get('html') or '')} 字节 HTML）")
                except urllib.error.HTTPError as e:
                    check(False, f"详情页渲染被拒（HTTP {e.code}）："
                                 f"{e.read().decode('utf-8', 'replace')[:140]}")

    # ---------------- 2. 两页脚本引用的 id 都存在 ----------------
    ids_ok(html, "题目列表页")
    if detail:
        ids_ok(detail, "题目详情页")

    # ---------------- 3. 操作列排一行 ----------------
    has_ops = 'class="prob-ops"' in html
    has_css = ".prob-ops { display: flex" in html and "flex-wrap: nowrap" in html
    check(has_ops and has_css,
          "操作列是 flex + nowrap（三个按钮排一行）" if (has_ops and has_css)
          else f"操作列排一行的样式没上全：标记 {has_ops}／CSS {has_css}"
               "（少了按钮就会折成两行）")

    # ---------------- 4. 删除走 AJAX，不整页跳转 ----------------
    has_json = "json=1" in html
    has_tbody = 'id="prob-rows"' in html
    has_flash = 'id="prob-flash"' in html
    check(has_json and has_tbody and has_flash,
          "删除走 AJAX（就地换表格：json=1 / prob-rows / prob-flash 都在）"
          if (has_json and has_tbody and has_flash)
          else f"删除还是会整页跳转：json=1 {has_json}／prob-rows {has_tbody}／"
               f"prob-flash {has_flash}（缺一样就会点完被弹回顶部）")
    check('onsubmit="return confirm' not in html,
          "删除不再是 form POST（表单提交会整页重载、滚动位置丢）")

    # ---------------- 5. 题目表格不出现横向滚动条 ----------------
    # 表格是 7 列，长标题（尤其「用在本场：…」那截）会把表格顶宽、出现横向滚动条。
    # 现在靠 `.csp-prob-table` 的固定布局 + 按列限宽 + 省略号截断压住，全文留在 title 里。
    # 这里同时盯**标记**和**CSS 规则**：只查一边的话，另一边被改回去照样会溢出。
    has_class = 'class="csp-prob-table"' in html
    has_css = ".csp-prob-table { table-layout: fixed" in html
    check(has_class and has_css,
          "题目表格用固定布局（不会出现横向滚动条）" if (has_class and has_css)
          else f"题目表格的固定布局没上全：标记 {has_class}／CSS 规则 {has_css}"
               "（少一个就会被长标题顶出横向滚动条）")

    # ---------------- 6. 内联 JS 的引号配对 ----------------
    # 这一类 bug 的来历：JS 源码写在 Python 三引号里，顺手写了单反斜杠 `\n`，
    # Python 把它变成**真换行** → JS 字符串没闭合 → 整段 <script> 语法错 →
    # **页面上所有按钮都点不动**（页面 200、元素都在，肉眼完全看不出来，浏览器里才发现）。
    # 判据：脚本里每一行的引号数必须是偶数（续行以 `\` 结尾的跳过）。
    bad_js = []
    for where, text in (("题目列表页", html), ("题目详情页", detail)):
        for blk in re.findall(r"<script>(.*?)</script>", text, re.S):
            for i, line in enumerate(blk.split("\n"), 1):
                if line.rstrip().endswith("\\"):
                    continue
                if line.count("'") % 2 or line.count('"') % 2:
                    bad_js.append(f"{where}第 {i} 行：{line.strip()[:60]}")
    check(not bad_js,
          "两页的内联 JS 引号都配对（不会整段脚本语法错、按钮点了没反应）" if not bad_js
          else "内联 JS 里有引号不配对的行（多半是 \\n 被 Python 变成了真换行）："
               + "；".join(bad_js[:4]))

    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
