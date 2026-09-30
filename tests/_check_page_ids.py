# -*- coding: utf-8 -*-
"""全站扫一遍：每个页面里 JS 引用到的 id 是否都真的存在。

（这次「查看题面」点了没反应就是这一类：`$('pv-sub')` 引用了一个从来没渲染出来的 id，
点击处理函数第一行抛 TypeError 整段死掉，页面 200、肉眼完全看不出来。）

⚠️ 这是**手工排查用**的，没进冒烟：学生端那几页要登录才能看到真内容，这里不带 cookie 去取，
多半拿到的是登录页（所以显示"0 个引用"，等于没查到）。要查学生端，先把 cookie 补上再跑。
    sudo python3 tests/_check_page_ids.py
"""
import os
import re
import sys
import urllib.parse
import urllib.request

R = "/root/csp-exam"
BASE = "http://127.0.0.1:8080"

sys.path.insert(0, R)


def pages(key, cid, kh):
    k = urllib.parse.quote(key)
    q = lambda **kw: "?" + urllib.parse.urlencode(kw)  # noqa: E731
    return [
        ("学生/考场列表", f"{BASE}/contests"),
        ("学生/比赛页", f"{BASE}/hall?c={cid}"),
        ("学生/题面页", f"{BASE}/problem?c={cid}&p=1"),
        ("学生/考生须知", f"{BASE}/help?c={cid}"),
        ("学生/查成绩", f"{BASE}/score?c={cid}"),
        ("管理/比赛列表", f"{BASE}/admin{q(key=key)}"),
        ("管理/本场管理", f"{BASE}/admin{q(key=key, c=cid)}"),
        ("管理/成绩总表", f"{BASE}/admin/scores{q(key=key, c=cid)}"),
        ("管理/提交详情", f"{BASE}/admin/student{q(key=key, c=cid, k=kh)}"),
        ("管理/名单分组", f"{BASE}/admin/groups{q(key=key)}"),
        ("管理/新建题目", f"{BASE}/admin/problem{q(key=key)}"),
        ("管理/题目列表", f"{BASE}/admin/problems{q(key=key)}"),
        ("管理/考号表", f"{BASE}/admin/print{q(key=key, c=cid)}"),
    ]


def scan(name, url):
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:                                  # noqa: BLE001
        return name, f"取页面失败：{e!r}", []
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    want = set(re.findall(r"\$\('([A-Za-z0-9_\-]+)'\)", html))
    want |= set(re.findall(r"getElementById\('([A-Za-z0-9_\-]+)'\)", html))
    # data-modal-open="x" 也是按 id 找的。注意 contest.js 里那句文档注释写的是
    # `[data-modal-open="id"]`，它会跟着内联进每一个页面 —— 那个 `id` 是占位符不是真 id，
    # 不排掉的话每页都会误报一条。
    want |= set(re.findall(r'data-modal-open="([A-Za-z0-9_\-]+)"', html))
    want -= {"id", "x", "ID"}
    missing = sorted(w for w in want if w not in ids)
    return name, f"{len(want)} 个引用", missing


def main():
    key = open(os.path.join(R, "data", "admin_key.txt")).read().strip()
    from csp_exam.core import store
    cs = store.list_contests()
    cid = cs[0]["id"] if cs else ""
    roster = store.load_roster(cid) if cid else {}
    kh = sorted(roster)[0] if roster else ""

    bad = 0
    for name, url in pages(key, cid, kh):
        label, note, missing = scan(name, url)
        if missing:
            bad += 1
            print(f"  [缺失] {label}：{missing}")
        else:
            print(f"  [OK]   {label}（{note}）")
    print()
    print(f"{bad} 个页面有「引用不到的元素」" if bad else "全部页面的 id 引用都对得上 ✓")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
