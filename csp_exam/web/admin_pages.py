"""管理端页面与接口（比赛/名单/分组/题目/成绩/提交详情/考号表/题单导入）

页面按原型改造（见 `整改清单.md` 2.2 与 `原型/admin/`）：

 * 比赛列表 —— 新建比赛带级别/时长；不再有「导入题单」块（新建题目已能选整个出题文件夹）
 * 本场管理 —— 「本场设置」块（级别/时长/前缀）+ 广东考区固定要求提示
* 题目列表（新页 `/admin/problems`）—— 题目编号/测试点/时限内存/大样例 + 查看题面 + 自己测试
* 提交详情 —— 测试点明细三层，点每行的**状态**能看这一点的输入/学生输出/标准答案
* 新建题目 —— 一个入口：选出题文件夹（或 zip）→ 自动识别 → 自动填表 → 建题

老师界面上的两个概念（别混）：

* **题目编号** —— `T` + 5 位数字（如 `T00001`），**建题时由系统分配、跟着题走、老师不能改**。
  老师只用它定位查找（「T00001 那道题」）。分配与登记在 `core/problems.py`
  （`assign_number`，落盘 `data/problem_codes.json`）。
* **英文名** —— 学生侧看到的名字（如 `candy`），老师在**新建 / 管理考试**时设置，
  存在本场 `exam.json` 的 `name` 字段里；学生用它建文件夹、命名源文件、写 freopen。
  （`store.code_of()` 读的就是它；`store.number_of()` 读的是上面的题目编号。）
"""

from __future__ import annotations


import traceback
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import html

from ..core import hydro_client as hydro, judgelocal, localoj, problems as make_problem, store
from ..core import importer as import_problemset
from ..core import wrapper
from ..core.grading import graded_cell, graded_text
from ..core.util import log, _fmt_bytes, _fmt_ms, _fmt_kb
from ..config import (HERE, MAX_PS_UPLOAD, PORT, PS_JOBS, PS_JOBS_LOCK,
                      HYDRO_ADMIN_PW_FILE)
from .urls import admin_url, cid_query, problem_detail_url, statement_url
from .ui import (page, rule_badge, render_upload_tree_html, md_to_html,
                 level_badge, modal, code_pre)


def _js_json(obj) -> str:
    """把对象变成能直接写进 `<script>` 的 JSON。

    转义 `<` `>` `&`：题目标题/题面里万一有 `</script>`，会把脚本截断
    （`_admin_problems` 里的题目数据也是这么处理的）。
    """
    text = json.dumps(obj, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(ch, esc)
    return text


# =====================================================================
# 题库元信息（测试点数 / 时限 / 内存 / 标程）——「题目列表」页要用
#
# 「题目列表」页要显示每道题的**测试点数、时限、内存**，还希望在「自己测试」里
# 用这道题的**标程**预填代码框。这三样东西此前没有任何地方落盘：评测站只存题面与
# 数据，`data/problem_codes.json` 只存编号。所以在管理端加一个小仓库
# `data/problem_info.json`，**建题成功时**（自己建题、题单导入两条路都走这里）
# 把当时就知道的数字记下来。
#
# 记不下来的题（更早建的、通过别的途径导入的）页面显示「—」，不影响任何功能。
# =====================================================================

PROBLEM_INFO_FILE = "problem_info.json"
#: 这个登记表最多留多少道题（按最近写入时间淘汰，防无限增长）
PROBLEM_INFO_MAX = 800

# 这份登记的**读写实现搬到了 `core/problems.py`**（`load_problem_info` / `save_problem_info`
# / `set_deleted` …）：core 的建题与题单导入也要知道"这道题老师已经删了、不用再认"，
# 而 core 不能反向 import web。这里保留同名别名，页面代码不用改一行；
# 也更不会出现"两处各写一份、字段对不上"的事。
load_problem_info = make_problem.load_problem_info
save_problem_info = make_problem.save_problem_info


def _problem_info_path() -> str:
    return make_problem.info_path()


def remember_problem(pid: str, *, title: str = "", code: str = "", name: str = "", cases=None,
                     time_ms=None, memory_mb=None, std: str = "") -> None:
    """建题成功后记下这道题的元信息（空值不覆盖已有的）。

    code —— 题目编号（T00001）；name —— 建题时填的默认英文名（可空）。
    """
    pid = str(pid or "").strip()
    if not pid:
        return
    with store._LOCK:
        items = load_problem_info()
        rec = dict(items.get(pid) or {})
        if title:
            rec["title"] = str(title)
        if code:
            rec["code"] = str(code)
        if name:
            rec["name"] = str(name)
        if cases:
            rec["cases"] = int(cases)
        if time_ms:
            rec["time_ms"] = int(time_ms)
        if memory_mb:
            rec["memory_mb"] = int(memory_mb)
        if std:
            rec["std"] = str(std)
        rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        items[pid] = rec
        if len(items) > PROBLEM_INFO_MAX:      # 只留最近的若干道
            order = sorted(items, key=lambda k: str((items[k] or {}).get("at", "")))
            for stale in order[:len(items) - PROBLEM_INFO_MAX]:
                items.pop(stale, None)
        save_problem_info(items)


def remember_meta(pid: str, *, title: str = "", name: str = "", time_ms=None,
                  memory_mb=None) -> dict:
    """改一道题的元信息（题目详情页的「保存修改」用）：题目名 / 英文名 / 时限 / 内存。

    为什么写**两处**（各自是某一列的权威来源，少写一处就会出现"改了没生效"）：

      * `data/problem_codes.json` —— 英文名 `name`、题目名 `title`。
        列表页的「英文名」列读的是 `name_of_pid(pid, codes)`，它**优先看这张表**；
        这张表里没有这条记录时才退回 `problem_info.json`。
      * `data/problem_info.json`（`remember_problem`）—— 题目名 / 测试点数 / 时限 / 内存 / 标程。
        `localoj.list_problems()`（题库清单）、`judgelocal.limits_of()`（判题时的时限内存）
        读的都是这里 —— 所以改时限内存**真的会改变判题**，不是只改个显示。

    返回 `{"ok", "changed", "skipped", "error"}`：`changed` 里是真正变了的字段，
    `skipped` 里是"你填了空/0，我没敢写"的字段 —— 页面照实说，
    没改动就说没改动，别谎报"已保存"（也别静悄悄地什么都不做）。

    为什么要挑着写：

      * **英文名可以清空**（留空就是没英文名，列表里显示空白，合法）。
      * **题目名不能清空** —— 清掉了老师在列表里就认不出哪道是哪道（列表退回显示题库标识）。
        要换名字就直接写新的；留空按"没改"处理，并且报出来。
      * **时限 / 内存必须正整数** —— 写 0 会让下一次判题变成"限时 0 毫秒"，全部 TLE，
        这种"一保存就把题废了"的操作不能干，按"没改"处理并报出来。
    """
    out = {"ok": False, "changed": [], "skipped": [], "error": ""}
    pid = str(pid or "").strip()
    if not pid:
        out["error"] = "没有指定题目标识"
        return out
    title = str(title or "").strip()
    name = str(name or "").strip()

    def _int(v):
        try:
            return int(str(v).strip() or 0)
        except (TypeError, ValueError):
            return 0

    time_ms, memory_mb = _int(time_ms), _int(memory_mb)
    changed, skipped = [], []
    codes = make_problem.load_codes()
    info0 = load_problem_info().get(pid) or {}
    rec = dict(codes.get(pid) or {})
    old_title = str(info0.get("title") or rec.get("title") or "")
    old_name = str(rec.get("name") or info0.get("name") or "")
    if not title:
        if old_title:
            skipped.append("题目名（留空没改 —— 清掉就认不出是哪道题了）")
    elif title != old_title:
        rec["title"] = title
        changed.append("题目名")
    if name != old_name:
        rec["name"] = name                      # 英文名允许清空
        changed.append("英文名")
    if changed:
        rec.setdefault("code", make_problem.code_of_pid(pid, codes) or "")
        rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        codes[pid] = rec
        make_problem.save_codes(codes)
    old_ms, old_mb = int(info0.get("time_ms") or 0), int(info0.get("memory_mb") or 0)
    if not time_ms:
        if old_ms:
            skipped.append("时限（要正整数，没改）")
        time_ms = 0
    elif time_ms != old_ms:
        changed.append("时限")
    if not memory_mb:
        if old_mb:
            skipped.append("内存（要正整数，没改）")
        memory_mb = 0
    elif memory_mb != old_mb:
        changed.append("内存")
    # 题目名/时限/内存落 `problem_info.json`（`remember_problem` 里空值不覆盖，正合适）
    remember_problem(pid, title=title, time_ms=time_ms or None, memory_mb=memory_mb or None)
    # 题库清单是**缓存**（列表页先读它）：不刷新的话，改了题目名要等下一次刷新才看得见
    try:
        store.save_catalog(localoj.list_problems())
    except (hydro.HydroError, OSError) as e:                    # noqa: BLE001
        log(f"[管理端] 改题目名后刷新题库缓存失败：{e!r}")
    out["ok"] = True
    out["changed"] = changed
    out["skipped"] = skipped
    return out


# =====================================================================
# 测试点明细（提交详情、题目列表的「自己测试」共用）
#
# 三层结构：① 状态条（✓✗TM 一行看全所有点）② 得分构成（按点给分，不捆绑）
# ③ 逐点明细表（每行的**状态是可点的**，点开看这一点的输入/学生输出/标准答案）。
# 判定符号与 CSS 类沿用 T-07 定的那一套（.tp-* 见 web/ui.py）。
# =====================================================================

#: 判定 →（状态条上的符号, CSS 类）
_TP_MARKS = {
    hydro.STATUS_AC: ("✓", "tp-ok"),
    2: ("✗", "tp-wa"),        # 答案错误
    3: ("T", "tp-tle"),       # 运行超时
    4: ("M", "tp-mle"),       # 内存超限
    5: ("✗", "tp-re"),        # 输出超限
    6: ("✗", "tp-re"),        # 运行错误
    7: ("✗", "tp-re"),        # 编译错误（有逐点数据时才会走到）
    8: ("!", "tp-re"),        # 系统错误
}

TP_LEGEND = ('<span class="tp-legend">'
             '<span><span class="tp-ok">✓</span> 答案正确</span>'
             '<span><span class="tp-wa">✗</span> 答案错误</span>'
             '<span><span class="tp-tle">T</span> 运行超时</span>'
             '<span><span class="tp-mle">M</span> 内存超限</span>'
             '<span><span class="tp-re">✗</span> 运行错误</span>'
             '<span><span class="tp-na">–</span> 未运行</span></span>')


def _tp_mark(status) -> tuple[str, str]:
    try:
        st = int(status)
    except (TypeError, ValueError):
        st = 0
    if st in _TP_MARKS:
        return _TP_MARKS[st]
    if st in hydro.STATUS_PENDING:
        return ("…", "tp-na")
    return ("✗", "tp-re")


def _tp_ok(case: dict) -> bool:
    return int((case or {}).get("status") or 0) == hydro.STATUS_AC


def _tp_strip(cases: list) -> str:
    """状态条：从左到右是第 1…N 个测试点。"""
    marks = []
    for i, case in enumerate(cases, 1):
        symbol, cls = _tp_mark(case.get("status"))
        title = "第 %d 个测试点：%s" % (i, case.get("status_text") or "")
        marks.append(f'<span class="{cls}" title="{html.escape(title, quote=True)}">{symbol}</span>')
    return '<div class="tp-strip">' + "".join(marks) + "</div>"


def _tp_summary(cases: list, full: int) -> str:
    """第 ② 层：得分构成（通过 X/Y 个测试点 × 每点 N 分）。"""
    n = len(cases)
    passed = sum(1 for c in cases if _tp_ok(c))
    got = sum(int(c.get("score") or 0) for c in cases)
    values = sorted({int(c.get("score") or 0) for c in cases if int(c.get("score") or 0)})
    if not values:
        per = f"{int(full) // n}" if n else "0"
    elif len(values) == 1:
        per = str(values[0])
    else:
        per = f"{values[0]}~{values[-1]}"
    cls = "ok" if n and passed == n else ("part" if got > 0 else "err")
    return (f'<div class="tp-card">'
            f'<p style="margin:0;font-size:15px">'
            f'通过 <b class="{"ok" if passed else "muted"}">{passed}</b> / {n} 个测试点，'
            f'每个测试点 <b>{per} 分</b> → 得 <b class="{cls}">{got} / {int(full)} 分</b></p>'
            f'<p class="muted" style="margin:6px 0 0">'
            f'<b>按测试点给分，过几个给几分</b>：通过了这个点就拿它的分值，'
            f'不用等整段数据都过（不捆绑子任务）。</p></div>')


def _tp_table(cases: list, pno) -> str:
    """第 ③ 层：逐点明细表。每行的**状态**是可点的链接（点开看这一点的内容）。"""
    head = ("<tr><th>测试点</th><th>状态</th><th>用时</th><th>内存</th>"
            "<th>得分</th><th>备注</th></tr>")
    # 评测机回传了子任务号时按它分组（只是方便看能力边界，不影响计分）
    subs = [str(c.get("subtask") or "").strip() for c in cases]
    group_it = len({s for s in subs if s}) >= 2

    rows, last_sub = [], None
    for i, case in enumerate(cases, 1):
        no = str(case.get("no") or i).zfill(2)
        sub = subs[i - 1]
        if group_it and sub != last_sub:
            last_sub = sub
            got = sum(int(c.get("score") or 0) for c in cases if str(c.get("subtask") or "").strip() == sub)
            cnt = sum(1 for c in cases if str(c.get("subtask") or "").strip() == sub)
            ok_n = sum(1 for c in cases
                       if str(c.get("subtask") or "").strip() == sub and _tp_ok(c))
            label = f"数据分组 {html.escape(sub)}" if sub else "未分组"
            rows.append(f'<tr class="tp-group"><td colspan="6"><b>{label}</b>'
                        f'<span class="tp-sub-score">{ok_n}/{cnt} 通过 · {got} 分</span></td></tr>')
        status = case.get("status_text") or hydro.status_text(case.get("status"))
        cls = "ok" if _tp_ok(case) else "err"
        score = int(case.get("score") or 0)
        note = case.get("message") or ""
        rows.append(
            f'<tr><td>{html.escape(no)}</td>'
            f'<td><a href="#" class="tp-open {cls}" data-tp="{html.escape(no, quote=True)}"'
            f' data-pno="{html.escape(str(pno), quote=True)}"'
            f' title="点开看这一点的输入、学生输出和标准答案">{html.escape(status)}</a></td>'
            f'<td>{html.escape(_fmt_ms(case.get("time")))}</td>'
            f'<td>{html.escape(_fmt_kb(case.get("memory")))}</td>'
            f'<td class="{cls}">{score}</td>'
            f'<td class="muted">{html.escape(note) or "—"}</td></tr>')
    return f'<table class="tp-table">{head}{"".join(rows)}</table>'


def _sig_text(text: str, limit: int = 60) -> str:
    """把一段输出压成一行（对比结论里引用它的时候用）。"""
    one = " ".join(str(text or "").split())
    return one if len(one) <= limit else one[:limit] + "…"


def _case_verdict(case: dict, full: int) -> str:
    """一句话说清「这一点的程序和标准答案差在哪」（整改清单 2.4）。

    数据是落盘的那一份：判定 / 学生输出 / 标准答案。分两种写法——
    过不了的点说清差在哪，过了的点直接说一致。
    """
    status = int(case.get("status") or 0)
    st_text = case.get("status_text") or hydro.status_text(status)
    out = str(case.get("output") or "")
    ans = str(case.get("answer") or "")
    score = int(case.get("score") or 0)
    # 注意：服务器是 Python 3.10/3.11，f-string 表达式里不能再用同类引号，值先算好
    used = ""
    if case.get("time"):
        used = "，用时 " + html.escape(_fmt_ms(case.get("time"))) \
               + "，内存 " + html.escape(_fmt_kb(case.get("memory")))
    note = (f'<br><span class="muted">判定：{html.escape(st_text)}'
            + used + f"，这一点 {score} 分。</span>")
    if case.get("truncated"):
        note += ('<br><span class="muted">内容过长已截断（只保留了前一部分）；'
                 '完整内容见服务器上的 <code>'
                 + html.escape(str(case.get("detail_file") or "data/contests/<比赛>/cases/"))
                 + '</code>。</span>')

    if _tp_ok(case):
        if not ans.strip():
            return ('<div class="flash flash-ok" style="margin:12px 0 0">'
                    '<b>这一点通过了。</b>不过这一点的标准答案是空的（题目只给了输入、没给 .out），'
                    '只说明程序正常跑完了。' + note + "</div>")
        return ('<div class="flash flash-ok" style="margin:12px 0 0">'
                '<b>学生输出与标准答案一致</b>，这一点拿到 '
                f'{score} 分。' + note + "</div>")

    parts = []
    if status == 3:
        parts.append("程序在时限内没跑完（<b>运行超时</b>）"
                     + ("，没有产生输出" if not out.strip() else "，输出只写了一半就被判超时"))
    elif status == 4:
        parts.append("<b>内存超限</b>，程序被评测机提前结束")
    elif status == 6:
        parts.append("<b>程序中途退出了（运行错误）</b>")
    elif status == 8:
        parts.append("<b>评测机返回系统错误</b>（多半不是代码问题，而是判分机没在工作）")
    if not out.strip():
        parts.append("学生这一点的输出是<b>空的</b>")
    if not ans.strip():
        parts.append("这一点的标准答案也是空的（题目只给了输入），"
                     "判定为错了说明程序没能正常跑完")
    elif not out.strip():
        parts.append(f"标准答案应该是 <b>{html.escape(_sig_text(ans))}</b>")
    else:
        o_lines, a_lines = out.rstrip("\n").split("\n"), ans.rstrip("\n").split("\n")
        diff = None
        for i in range(max(len(o_lines), len(a_lines))):
            o = o_lines[i] if i < len(o_lines) else None
            a = a_lines[i] if i < len(a_lines) else None
            if o != a:
                diff = (i, o, a)
                break
        if diff is None:
            parts.append("两边内容其实一样（可能只差行尾空格或最后的空行），"
                         "但评测判定是「" + html.escape(st_text) + "」")
        else:
            i, o, a = diff
            if o is None:
                parts.append(f"学生只输出了 {len(o_lines)} 行，标准答案有 {len(a_lines)} 行——"
                             f"<b>少了后面的内容</b>；第 {i + 1} 行标准答案是 "
                             f"<b>{html.escape(_sig_text(a or ''))}</b>")
            elif a is None:
                parts.append(f"学生输出了 {len(o_lines)} 行，标准答案只有 {len(a_lines)} 行——"
                             f"<b>多输出了内容</b>；多的第一行是 "
                             f"<b>{html.escape(_sig_text(o or ''))}</b>")
            else:
                parts.append(f"第 {i + 1} 行就不一样：学生输出的是 "
                             f"<b>{html.escape(_sig_text(o))}</b>，"
                             f"标准答案是 <b>{html.escape(_sig_text(a))}</b>")
    head = ('<div class="flash flash-err" style="margin:12px 0 0">'
            '<b>这一点的程序和标准答案差在哪</b><br>' + "。".join(parts) + "。")
    return head + note + "</div>"


def _tp_detail_modal() -> str:
    """测试点详情的悬浮窗外壳（提交详情、题目列表的「自己测试」共用）。"""
    body = """
<div class="card" style="padding:12px 15px;margin-top:0">
  <div class="kv">
    <div><b>测试点</b><span id="tp-no">—</span></div>
    <div><b>状态</b><span id="tp-status">—</span></div>
    <div><b>用时</b><span id="tp-time">—</span></div>
    <div><b>内存</b><span id="tp-mem">—</span></div>
    <div><b>得分</b><span id="tp-score">—</span></div>
  </div>
</div>
<div id="tp-verdict"></div>
<h3 style="font-size:15px;margin:16px 0 6px">输入数据 <span class="muted" id="tp-in-note"></span></h3>
<pre class="code-view" id="tp-in">—</pre>
<h3 style="font-size:15px;margin:16px 0 6px">学生输出 <span class="muted" id="tp-out-note"></span></h3>
<pre class="code-view" id="tp-out">—</pre>
<h3 style="font-size:15px;margin:16px 0 6px">标准答案 <span class="muted" id="tp-ans-note"></span></h3>
<pre class="code-view" id="tp-ans">—</pre>
<p class="muted" id="tp-foot"></p>
"""
    return modal("tp-modal", "测试点详情", body, sub="输入 / 学生输出 / 标准答案")


#: 测试点详情的取数与填充脚本（放在页面里；`window.CSP_TP_QUERY` 由页面下发）。
TP_JS = """<script>
(function () {
  var MAX_LINES = 15;
  window.cspTpText = function (el, noteEl, text, suffix, missing) {
    if (!el) return;
    text = (text === null || text === undefined) ? '' : String(text);
    if (!text && missing) {            /* 评测机压根没存这一项，别说成"学生没有输出" */
      el.textContent = '（' + missing + '）';
      if (noteEl) noteEl.textContent = '';
      return;
    }
    var lines = text.split('\\n');
    var cut = lines.length > MAX_LINES + 20;
    el.textContent = text ? (cut ? lines.slice(0, MAX_LINES).join('\\n') : text) : '（空）';
    if (noteEl) {
      noteEl.textContent = cut ? '　共 ' + lines.length + ' 行，只显示前 ' + MAX_LINES + ' 行'
                               : (suffix || '');
    }
  };
  window.cspTpOpen = function (a) {
    var q = (window.CSP_TP_QUERY || '') + '&n=' + encodeURIComponent(a.getAttribute('data-pno') || '')
          + '&t=' + encodeURIComponent(a.getAttribute('data-tp') || '');
    fetch('/admin/testcase' + q).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { alert(d.error || '取不到这一点的详情'); return; }
      var set = function (id, text) { var el = document.getElementById(id); if (el) el.textContent = text; };
      set('tp-no', d.no + (d.title ? '（' + d.code + ' ' + d.title + '）' : ''));
      var st = document.getElementById('tp-status');
      if (st) st.innerHTML = '<span class="' + (d.cls || '') + '">' + (d.status_text || '') + '</span>';
      set('tp-time', d.time_text || '—');
      set('tp-mem', d.memory_text || '—');
      set('tp-score', d.score + (d.full ? ' / ' + d.full : '') + ' 分');
      var verdict = document.getElementById('tp-verdict');
      if (verdict) verdict.innerHTML = d.verdict || '';
      /* has_content=false 表示评测机没存内容（Hydro 的记录里只有判定/用时/内存）。
         这时候三块都要说清"是没存"，不能显示成"空的"——否则会被读成学生没输出。 */
      var miss = (d.has_content === false) ? '评测机没有保存这一点的内容' : '';
      window.cspTpText(document.getElementById('tp-in'), document.getElementById('tp-in-note'),
                       d.input, '　这一点的输入数据', miss);
      window.cspTpText(document.getElementById('tp-out'), document.getElementById('tp-out-note'),
                       d.output, d.output ? '' : '　（学生没有输出）', miss);
      window.cspTpText(document.getElementById('tp-ans'), document.getElementById('tp-ans-note'),
                       d.answer, '　这一点的期望输出', miss);
      var foot = document.getElementById('tp-foot');
      if (foot) foot.textContent = d.foot || '';
      if (window.openModal) window.openModal('tp-modal');
    }).catch(function (e) {
      alert('取这一点详情失败：' + e);
    });
  };
  document.addEventListener('click', function (e) {
    var t = e.target;
    var a = (t && t.closest) ? t.closest('a.tp-open[data-tp]') : null;
    if (!a) return;
    e.preventDefault();
    window.cspTpOpen(a);
  });
})();
</script>"""


def _tp_block(cases: list, full: int, pno) -> str:
    """测试点明细三层（状态条 → 得分构成 → 逐点明细表）。"""
    if not cases:
        return ""
    return ('<h3 style="font-size:15px;margin:16px 0 6px">测试点总览</h3>'
            + _tp_strip(cases) + " " + TP_LEGEND
            + '<h3 style="font-size:15px;margin:16px 0 6px">得分构成</h3>'
            + _tp_summary(cases, full)
            + '<h3 style="font-size:15px;margin:16px 0 6px">逐点明细</h3>'
            + '<p class="muted" style="margin:0 0 8px">'
              '点每行的<b>状态</b>可以看这一点的输入数据、学生输出和标准答案。</p>'
            + _tp_table(cases, pno))


# =====================================================================
# 广东考区的固定要求（考号格式 / 个人信息文件 / 命名红线）
# 原型里这块在每个页面上都以 .rule-warn 出现，文案统一放这里。
# =====================================================================

GD_RULES = """<div class="rule-warn">
  <b>广东考区固定要求</b>
  <ul>
    <li>考号格式 <code>GD-S</code> + 5 位随机数（提高级）／<code>GD-J</code> + 5 位随机数（入门级），
        减号是<b>半角</b>、字母<b>全大写</b></li>
    <li>学生要交个人信息文件：文件名＝学生姓名，内容含姓名/性别/年级/地区/学校/辅导老师/提交的程序</li>
    <li>交卷目录：考号目录 → 题目<b>英文名</b>子目录 → 英文名.cpp
        （英文名在配题时填，如 <code>candy/candy.cpp</code>）</li>
    <li>命名严格区分大小写，<code>_</code> 与 <code>-</code> 不可混用，文件夹名不得有空格</li>
  </ul>
</div>"""


# =====================================================================
# 题目列表（新页）：「查看题面」与「自己测试」
#
# 两道工序都在**服务端**取好数据，页面自己只负责显示：
#   * 题面 —— 用 `core/markdown.md_to_html()` 渲染（和题面页一份逻辑），再塞进悬浮窗
#   * 自己测试 —— POST /admin/selftest 以评测站管理员身份提交代码，跑完全部测试点后
#     把逐点结果、最慢的点与时限占比一起回给页面（最慢超过时限 50% 会给出预警）
# =====================================================================


def _math_js() -> str:
    """题面里的公式（$…$ / $$…$$）在悬浮窗里也要渲染。

    题面页靠 `page(math=True)` 自动渲染，但它只认页面上第一个 `.stmt`——题目列表里
    题面是按需填进悬浮窗的，所以这里手动补一次渲染（delimiters 与 ui.page 里一致）。
    """
    return """<script>
function cspRenderMath(root) {
  if (!root || !window.renderMathInElement) return;
  try {
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\\\[", right: "\\\\]", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\\\(", right: "\\\\)", display: false }
      ],
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"]
    });
  } catch (e) { /* 渲染失败不影响阅读（会原样显示 $...$） */ }
}
</script>"""


#: 题目列表页的交互（查看题面 / 自己测试）。数据在 `window.CSP_CAND` 里，页面里下发。
PROBLEMS_JS = """<script>
(function () {
  var CAND = window.CSP_CAND || {};
  function $(id) { return document.getElementById(id); }

  /* ---------------- 删除 / 恢复 / 彻底删除 ----------------
     一律走 fetch，拿服务端回的新表格 HTML 就地换掉：**页面不跳转、滚动位置不丢**。
     以前这三样是 form POST + 302 回列表页，点完删除整页重载、人会被弹回页面顶部，
     刚删的那行去哪了也看不见（这一版专门改的就是这个）。 */
  var ACT_ASK = {
    'delete': function (t, pid) {
      return '确定删除题目 ' + t + '（' + pid + '）？\\n只是从列表里收起来 —— '
           + '题面和历史提交记录都还看得到，随时可以恢复。';
    },
    'restore': function (t, pid) {
      return '把题目 ' + t + '（' + pid + '）放回「全部题目」？';
    },
    'purge': function (t, pid) {
      return '彻底删除 ' + pid + '？\\n题目数据、题目编号、题面缓存、大样例都会一起删掉，'
           + '删了就找不回来了；历史提交记录里这道题会显示为已删除。';
    }
  };

  function probFlash(text, kind) {
    var box = $('prob-flash');
    if (!box) return;
    box.innerHTML = text
      ? '<div class="flash flash-' + (kind || 'ok') + '">' + text + '</div>' : '';
  }

  function probAct(btn, what) {
    var attr = what === 'delete' ? 'data-del' : (what === 'restore' ? 'data-restore' : 'data-purge');
    var pid = btn.getAttribute(attr) || '';
    var title = btn.getAttribute('data-title') || pid;
    if (!pid || !ACT_ASK[what]) return;
    if (!confirm(ACT_ASK[what](title, pid))) return;
    var tr = btn.closest ? btn.closest('tr') : null;
    if (tr) tr.classList.add('row-busy');            /* 先灰掉，别让人以为没点上 */
    var url = '/admin/problem' + (window.CSP_KEY_QUERY || '');
    url += (url.indexOf('?') >= 0 ? '&' : '?') + 'json=1';
    var body = new URLSearchParams();
    body.set('action', what);
    body.set('pid', pid);
    fetch(url, { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (tr) tr.classList.remove('row-busy');
        if (!d || !d.ok) { probFlash((d && (d.message || d.error)) || '没成功', 'err'); return; }
        var tb = $('prob-rows');
        if (tb && typeof d.rows === 'string') tb.innerHTML = d.rows;
        var del = $('prob-del');                       /* 整块换：刚删的题就在这堆里 */
        if (del && typeof d.deleted_html === 'string' && del.parentNode) {
          var tmp = document.createElement('div');
          tmp.innerHTML = d.deleted_html;
          var next = tmp.firstElementChild;
          if (next) del.parentNode.replaceChild(next, del);
        }
        var cnt = $('prob-live-count');
        if (cnt && typeof d.live_count === 'number') cnt.textContent = d.live_count;
        probFlash(d.message, 'ok');
      })
      .catch(function (e) {
        if (tr) tr.classList.remove('row-busy');
        probFlash('操作失败：' + e, 'err');
      });
  }

  /* 事件委托：表格被整块换掉以后，新行里的按钮照样点得动 */
  document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest
      ? e.target.closest('[data-del],[data-restore],[data-purge]') : null;
    if (!btn) return;
    e.preventDefault();
    probAct(btn, btn.hasAttribute('data-del') ? 'delete'
              : (btn.hasAttribute('data-restore') ? 'restore' : 'purge'));
  });

  /* ---------------- 自己测试 ---------------- */
  var cur = '';
  function refreshCount() {
    var v = $('test-src').value;
    $('test-count').textContent = (v ? v.split('\\n').length : 0) + ' 行 · ' + v.length + ' 字符';
  }
  var src = $('test-src');
  if (src) {
    src.addEventListener('input', refreshCount);
    src.addEventListener('keydown', function (e) {           /* Tab 插 4 个空格 */
      if (e.key !== 'Tab') return;
      e.preventDefault();
      var s = this.selectionStart, t = this.selectionEnd;
      this.value = this.value.slice(0, s) + '    ' + this.value.slice(t);
      this.selectionStart = this.selectionEnd = s + 4;
      refreshCount();
    });
  }
  function openTest(btn) {
      var key = btn.getAttribute('data-test');
      var p = CAND[key] || {};
      cur = key;
      $('test-sub').textContent = '· ' + (p.code || '') +
        (p.name ? ' ' + p.name : '') + ' ' + (p.title || '');
      /* 学生交的文件名用的是**英文名**（没有就退回编号） */
      $('test-file').textContent = (p.name || p.code || 'std') + '.cpp';
      $('test-hint').textContent = p.cases
        ? ('　会编译并跑完全部 ' + p.cases + ' 个测试点')
        : '　会编译并跑完这道题的全部测试点';
      if (p.std) {
        $('test-src').value = p.std;
        $('test-note').textContent = '框里默认填的是这道题的标程，可以直接改，改完再跑一遍。';
      } else {
        $('test-src').value = '';
        $('test-note').textContent = '这道题的标程没有存在考试服务里，把代码粘进来再跑。';
      }
      refreshCount();
      $('test-result').hidden = true;
      if (window.openModal) window.openModal('test-modal');
  }

  document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('[data-test]') : null;
    if (btn) openTest(btn);
  });
  var run = $('test-run');
  if (run) run.addEventListener('click', function () {
    if (!cur) return;
    var hint = $('test-hint');
    hint.innerHTML = '　<span class="muted">正在编译并评测…（这道题有多少测试点就跑多少，稍等）</span>';
    var body = new URLSearchParams();
    body.set('pid', cur);
    body.set('source', $('test-src').value);
    fetch('/admin/selftest' + (window.CSP_KEY_QUERY || ''), { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) {
          hint.innerHTML = '　<span class="err">' + (d.error || '测试失败') + '</span>';
          return;
        }
        hint.textContent = '　跑完了：' + d.passed + '/' + d.total + ' 个点通过';
        $('test-strip').innerHTML = (d.cases || []).map(function (c) {
          return '<span class="' + c.cls + '" title="第 ' + c.no + ' 点：' + c.status_text + '">'
               + c.mark + '</span>';
        }).join('');
        $('test-table').innerHTML = '<tr><th>测试点</th><th>状态</th><th>用时</th><th>内存</th></tr>' +
          (d.cases || []).map(function (c) {
            return '<tr><td>' + c.no + '</td><td class="' + (c.cls === 'tp-ok' ? 'ok' : 'err') + '">'
                 + c.status_text + '</td><td>' + c.time_text + '</td><td>' + c.memory_text + '</td></tr>';
          }).join('');
        $('test-summary').className = 'tp-card ' + (d.passed === d.total ? 'tp-pass' : 'tp-fail');
        $('test-verdict').innerHTML = d.verdict || '';
        $('test-result').hidden = false;
      })
      .catch(function (e) { hint.innerHTML = '　<span class="err">测试失败：' + e + '</span>'; });
  });
})();
</script>"""


#: 题目详情页的交互：右边跟着左边改、视图切换、编辑小习惯（Tab / Ctrl+S）。
PROBLEM_DETAIL_JS = """<script>
(function () {
  var wrap = document.getElementById('pd-wrap');
  var src = document.getElementById('pd-src');
  var box = document.getElementById('pd-render');
  var note = document.getElementById('pd-note');
  if (!wrap || !src || !box) return;

  /* ---------------- 视图：左右对照 / 只看编辑 / 只看成品 ----------------
     记在 localStorage 里：小屏（或只想专心改题面时）选了「只看左边」，
     刷新、保存之后还是那个视图，不用每次重选。 */
  var MODE_KEY = 'csp-pd-mode';
  function setMode(mode) {
    if (['both', 'left', 'right'].indexOf(mode) < 0) mode = 'both';
    wrap.setAttribute('data-mode', mode);
    document.querySelectorAll('[data-pd-mode]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-pd-mode') === mode);
    });
    try { localStorage.setItem(MODE_KEY, mode); } catch (e) { /* 隐私模式忽略 */ }
  }
  document.querySelectorAll('[data-pd-mode]').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-pd-mode')); });
  });
  var saved = 'both';
  try { saved = localStorage.getItem(MODE_KEY) || 'both'; } catch (e) { /* 忽略 */ }
  setMode(saved);

  /* ---------------- 右边跟着左边改 ----------------
     渲染**在服务端**（core/markdown.py，和题面页同一份逻辑）——
     前端不引 markdown 库，也就不会出现"预览挺好看、学生看到的却是另一回事"。 */
  var timer = null, seq = 0, lastSent = null;
  function render() {
    var text = src.value;
    if (text === lastSent) return;
    lastSent = text;
    var mine = ++seq;
    var body = new URLSearchParams();
    body.set('render', '1');
    body.set('statement', text);
    fetch('/admin/scan' + (window.CSP_KEY_QUERY || ''), { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (mine !== seq) return;                       /* 慢的那个回包丢掉 */
        if (!d.ok) { if (note) note.textContent = '　' + (d.error || '渲染失败'); return; }
        box.innerHTML = d.html || '<p class="muted">（题面还是空的）</p>';
        if (window.cspRenderMath) cspRenderMath(box);
        if (note) note.textContent = '　右边已跟着更新 · ' +
          (text ? text.split('\\n').length + ' 行 / ' + text.length + ' 字符' : '空');
      })
      .catch(function (e) { if (note) note.textContent = '　渲染失败：' + e; });
  }
  src.addEventListener('input', function () {
    if (note) note.textContent = '　正在渲染…';
    clearTimeout(timer);
    timer = setTimeout(render, 400);
  });

  /* ---------------- 编辑小习惯 ----------------
     Tab 插 4 个空格（写 Markdown 的缩进/代码块要），Ctrl+S 保存 */
  src.addEventListener('keydown', function (e) {
    if (e.key === 'Tab') {
      e.preventDefault();
      var s = this.selectionStart, t = this.selectionEnd;
      this.value = this.value.slice(0, s) + '    ' + this.value.slice(t);
      this.selectionStart = this.selectionEnd = s + 4;
      clearTimeout(timer);
      timer = setTimeout(render, 400);
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      var form = document.getElementById('pd-form');
      if (form) form.submit();
    }
  });
})();
</script>"""


def _test_modal() -> str:
    """「自己测试」的悬浮窗（填代码 → 跑全部测试点 → 逐点结果 + 时限预警）。"""
    body = """
<p class="muted" style="margin-top:0">
  用这份代码跑一遍这道题的<b>全部测试点</b>，看数据、时限、内存设置有没有问题。
</p>
<div class="codebox-bar">
  <span><span class="ok">✓</span> <span id="test-file">标程.cpp</span> · <span id="test-count">—</span></span>
  <span class="muted" id="test-note"></span>
</div>
<textarea class="codebox" id="test-src" spellcheck="false"></textarea>
<p style="margin-top:12px">
  <button type="button" class="btn" id="test-run">开始测试</button>
  <span class="muted" id="test-hint">　会编译并跑完全部测试点</span>
</p>
<div id="test-result" hidden>
  <h3 style="font-size:15px;margin:16px 0 6px">测试结果</h3>
  <div class="tp-card tp-pass" id="test-summary">
    <div class="tp-strip" id="test-strip"></div>
    <p style="margin:8px 0 0">""" + TP_LEGEND + """</p>
  </div>
  <table class="tp-table" id="test-table">
    <tr><th>测试点</th><th>状态</th><th>用时</th><th>内存</th></tr>
  </table>
  <div id="test-verdict"></div>
</div>
"""
    return modal("test-modal", "自己测试", body, sub="跑一遍全部测试点，验证数据与时限")


def _view_modal() -> str:
    """「查看题面」的悬浮窗（内容由页面 JS 填；按学生视角渲染 Markdown + 公式）。"""
    body = """
<div class="card">
  <p class="muted" style="margin-top:0">考号 <b>GD-S00001</b>（示例学生） · 返回比赛 · 下载大样例 · 退出</p>
  <p><span class="tag tag-csp">CSP 赛制</span> <span class="tag tag-level">提高级 S 组</span></p>
  <h2 id="pv-head"></h2>
  <div class="card stmt" id="pv-body"></div>
</div>
<p class="muted">这就是学生在考试页点开题面时看到的样子（题面里的公式会真的渲染出来）。</p>
"""
    return modal("pv-modal", "题面", body, sub="学生在考试页看到的样子")




class AdminPages:
    """管理端的所有页面与接口（由 web.server.Handler 混入）。"""

    def _admin_login(self):
        self._send(page("管理端", self._flash("err", "需要管理密钥。密钥在服务器 "
                                                   "/root/csp-exam/data/admin_key.txt。") +
                        '<p class="muted">从评测站导航「比赛」进入可免密钥（登录过管理员后会自动记住）。</p>'),
                   403)

    def _admin_nav(self, key: str = "", cid: str = "") -> str:
        """管理端顶部那排快捷入口。

        **常驻**（`position: sticky`，见 CSS 的 `.admin-nav`）：页面往下滚也一直贴在顶部，
        省得改完题目表还要滚回最上面点「本场管理」。
        """
        links = ['<a href="' + admin_url(key) + '">比赛列表</a>']
        if cid:
            links.append(f'<a href="{admin_url(key, cid)}">本场管理</a>')
            links.append(f'<a href="/admin/scores{html.escape(cid_query(key, cid))}">成绩总表</a>')
            links.append(f'<a href="/admin/print{html.escape(cid_query(key, cid))}" target="_blank">考号表</a>')
        links.append(f'<a href="{admin_url(key, path="/admin/groups")}">名单分组</a>')
        links.append(f'<a href="{admin_url(key, path="/admin/problems")}">题目列表</a>')
        links.append(f'<a href="{admin_url(key, path="/admin/problem")}">新建题目</a>')
        links.append('<a href="/docs/problemset" target="_blank">导入题单文档</a>')
        links.append('<a href="/" target="_blank">学生端入口</a>')
        return '<nav class="muted admin-nav">管理端 · ' + " · ".join(links) + "</nav>"

    def _admin_home(self, q: dict, cookie: str = "", flash: str = ""):
        key = q.get("key", "")
        # ---- 按「名单分组」看比赛：哪个组的成员被分到了哪几场 ----
        # 分组里存的是**姓名**（groups.json 的 students），比赛名单里也只有考号→姓名，
        # 两边都没有"这个学生属于哪个组"的字段 —— 所以按姓名匹配。一个人在多个组里
        # （集训班 + 周末班）就**每个组都算他一份**：这样"南山S 的成员上了哪几场"才是全的。
        # 谁的分组都匹配不上（名单是手打的）→ 归「（未分组）」。
        groups = store.load_groups()
        name2groups: dict[str, list[str]] = {}
        for g in groups:
            gname = str(g.get("name") or g.get("gid") or "")
            for nm in (g.get("students") or []):
                name2groups.setdefault(str(nm), []).append(gname)
        OTHER = "（未分组）"

        def breakdown(roster: dict) -> dict:
            """这场比赛的名单里，每个分组各有多少人。"""
            cnt: dict[str, int] = {}
            for rec in (roster or {}).values():
                gs = name2groups.get(str((rec or {}).get("name") or ""))
                for gname in (gs or [OTHER]):
                    cnt[gname] = cnt.get(gname, 0) + 1
            return cnt

        # 筛选：勾了哪几个组（多选）。用 `g_<gid>=1` 这种**一个组一个字段名**的写法，
        # 浏览器一次提交多个勾选框也不会互相覆盖（后端的 _query() 每个键只留第一个值，
        # 用 `g=g1&g=g2` 会只剩 g1 —— 踩过这个坑）。
        sel = {str(g.get("name") or g.get("gid") or "")
               for g in groups if q.get("g_" + str(g.get("gid") or ""))}
        want_other = bool(q.get("g__none"))
        filtering = bool(sel or want_other)

        def matched(bd: dict) -> bool:
            if not filtering:
                return True
            if want_other and bd.get(OTHER):
                return True
            return any(bd.get(n) for n in sel)

        rows = []
        hits: dict[str, int] = {}                 # 每个组一共上了多少场（不受筛选影响）
        n_all = 0
        for c in sorted(store.list_contests(),
                        key=lambda c: (str(c.get("created_at") or ""), str(c.get("id") or "")),
                        reverse=True):
            roster = store.load_roster(c["id"])
            results = store.load_results(c["id"])
            exam = store.load_exam(c["id"])
            rel = bool(exam.get("released"))
            bd = breakdown(roster)
            n_all += 1
            for gname in bd:
                hits[gname] = hits.get(gname, 0) + 1
            if not matched(bd):
                continue
            # 这一场里各组分到多少人：勾中的组用徽章高亮，没勾的淡着显示
            chips = " · ".join(
                (f'<span class="tag">{html.escape(gname)} ×{n}</span>'
                 if (gname in sel or (gname == OTHER and want_other))
                 else f'<span class="muted">{html.escape(gname)} ×{n}</span>')
                for gname, n in sorted(bd.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
            # 「发布成绩」就放在比赛旁边：老师考完直接在这里公布，不用点进比赛里找。
            # 公布后学生立刻能在「我的比赛 / 查成绩」看到自己的分数（「我的提交」页本轮已删）。
            rel_btn = (
                f'<form method="post" action="/admin/release{cid_query(key, c["id"])}" style="display:inline" '
                f'onsubmit="return confirm(\'{"收回后学生将看不到分数，确定？" if rel else "发布后学生立刻能看到自己的分数（比赛进行中也生效），确定？"}\')">'
                f'<input type="hidden" name="released" value="{0 if rel else 1}">'
                f'<button class="btn btn-sm{" btn-gray" if rel else ""}" type="submit">'
                f'{"收回成绩" if rel else "发布成绩"}</button></form> ')
            rows.append(
                f'<tr><td>{rule_badge(c)}</td><td>{level_badge(c)}</td>'
                f'<td><b>{html.escape(c["title"])}</b>'
                + (f'<br><span style="font-size:12.5px">{chips}</span>' if chips else "")
                + f'</td>'
                f'<td>{len(roster)}</td><td>{len(results)}</td>'
                f'<td>{"开放" if c.get("open", True) else "关闭"} / '
                f'{"已公布" if rel else "未公布"}</td>'
                f'<td class="muted">{html.escape(c.get("created_at", ""))}</td>'
                f'<td><a class="btn btn-sm" href="/admin{cid_query(key, c["id"])}">管理</a> '
                f'<a class="btn btn-sm btn-gray" href="/admin/scores{cid_query(key, c["id"])}">成绩</a> '
                f'{rel_btn}'
                f'<form method="post" action="/admin/delete{cid_query(key, c["id"])}" style="display:inline" '
                f'onsubmit="return confirm(\'确定删除这场比赛？名单、成绩、提交的代码都会一起删除。\')">'
                f'<button class="btn btn-sm btn-danger" type="submit">删除</button></form></td></tr>')
        n_matched = len(rows)
        page_size = 10
        page_count = max(1, (n_matched + page_size - 1) // page_size)
        try:
            current_page = int(q.get("page", "1"))
        except (ValueError, TypeError):
            current_page = 1
        current_page = max(1, min(current_page, page_count))
        offset = (current_page - 1) * page_size
        rows = rows[offset:offset + page_size]
        show_txt = f"，符合条件 {n_matched} 场" if filtering else ""

        def page_url(number: int) -> str:
            params = {"page": str(number)}
            if key:
                params["key"] = key
            for group in groups:
                field = "g_" + str(group.get("gid") or "")
                if q.get(field):
                    params[field] = "1"
            if want_other:
                params["g__none"] = "1"
            return html.escape("/admin?" + urllib.parse.urlencode(params), quote=True)

        page_links = []
        if current_page > 1:
            page_links.append(f'<a class="btn btn-sm btn-gray" href="{page_url(current_page - 1)}">上一页</a>')
        numbers = sorted({1, page_count, *range(max(1, current_page - 2), min(page_count, current_page + 2) + 1)})
        last = 0
        for number in numbers:
            if last and number > last + 1:
                page_links.append('<span class="muted">…</span>')
            if number == current_page:
                page_links.append(f'<span class="btn btn-sm" aria-current="page">{number}</span>')
            else:
                page_links.append(f'<a class="btn btn-sm btn-gray" href="{page_url(number)}">{number}</a>')
            last = number
        if current_page < page_count:
            page_links.append(f'<a class="btn btn-sm btn-gray" href="{page_url(current_page + 1)}">下一页</a>')
        pagination = (f'<nav class="row contest-pagination" aria-label="比赛分页">'
                      f'<span class="muted">共 {n_matched} 场 · 每页 {page_size} 场 · '
                      f'第 {current_page} / {page_count} 页</span> ' + " ".join(page_links) + '</nav>')
        # 下拉里的勾选框（**没有 JS 时面板是展开的**，退化成普通勾选框照样能用；
        # 有 JS 才收成下拉 —— 点按钮展开、勾完点「确定」提交）。
        boxes = []
        for g in groups:
            gid = str(g.get("gid") or "")
            gname = str(g.get("name") or gid)
            boxes.append(
                f'<label><input type="checkbox" name="g_{html.escape(gid, quote=True)}" value="1"'
                + (" checked" if gname in sel else "")
                + f'> <span class="gmul-name">{html.escape(gname)}</span>'
                + f'<span class="muted"> {len(g.get("students") or [])} 人'
                + f' · {hits.get(gname, 0)} 场</span></label>')
        if hits.get(OTHER):
            boxes.append(
                '<label><input type="checkbox" name="g__none" value="1"'
                + (" checked" if want_other else "")
                + f'> <span class="gmul-name">{OTHER}</span>'
                + f'<span class="muted"> {hits.get(OTHER, 0)} 场</span></label>')
        boxes_html = "".join(boxes) or \
            '<p class="muted" style="margin:0">还没有名单分组，可先去「管理名单分组」新建一批学生。</p>'
        picked = [str(x.get("name") or x.get("gid") or "") for x in groups if
                  str(x.get("name") or x.get("gid") or "") in sel]
        if want_other:
            picked.append(OTHER)
        gmul_txt = ("全部分组" if not picked else
                    ("、".join(picked[:2]) + (f" 等 {len(picked)} 组" if len(picked) > 2 else "")))
        body = f"""
{self._flash("ok", flash) if flash else ""}
{self._admin_nav(key)}
<p class="muted">名单分组是"一批学生"，可以反复用在不同考试里；每场考试的考号都会重新随机分配。
 <a class="btn btn-sm btn-gray" href="{admin_url(key, path='/admin/groups')}">管理名单分组</a></p>
<h2>已有比赛（{n_all} 场）</h2>
<div class="card">
  <form method="get" action="/admin" class="contest-toolbar" style="margin:0">
    <input type="hidden" name="key" value="{html.escape(key, quote=True)}">
    <div class="gmul">
      <button type="button" class="btn btn-sm btn-gray" id="gmul-btn" aria-expanded="false" aria-controls="gmul-panel">分组：{html.escape(gmul_txt)} ▾</button>
      <div class="gmul-panel" id="gmul-panel">
        <p class="muted" style="margin:0 0 6px"><b>按名单分组分类</b>（可多选）——
          只显示「勾中的组里有成员」的比赛；一个人同时在两个组里就两边都算。</p>
        <div class="gmul-options">{boxes_html}</div>
        <div class="gmul-foot">
          <button type="submit" class="btn btn-sm">确定</button>
          <a class="btn btn-sm btn-gray" href="/admin{cid_query(key, "")}">清空</a>
          <span class="muted">共 {n_all} 场{show_txt}</span>
        </div>
      </div>
    </div>
    <a class="btn btn-sm" href="{admin_url(key, path='/admin/new')}">新建比赛</a>
    <span class="muted contest-list-note">按创建时间从新到旧 · ×N 表示该组参赛人数</span>
  </form>
</div>
<script>
(function () {{
  var btn = document.getElementById('gmul-btn');
  var panel = document.getElementById('gmul-panel');
  if (!btn || !panel) return;
  panel.hidden = true;                       /* 有 JS 才收成下拉；没 JS 时它是展开的 */
  function toggle(show) {{
    panel.hidden = (show === undefined) ? !panel.hidden : !show;
    btn.setAttribute('aria-expanded', String(!panel.hidden));
  }}
  btn.addEventListener('click', function (e) {{ e.stopPropagation(); toggle(); }});
  panel.addEventListener('click', function (e) {{ e.stopPropagation(); }});
  document.addEventListener('click', function () {{ toggle(false); }});
  document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape' && !panel.hidden) {{ toggle(false); btn.focus(); }} }});
  function refresh() {{
    var picked = [];
    panel.querySelectorAll('input[type=checkbox]').forEach(function (b) {{
      if (b.checked) {{
        var n = b.parentElement.querySelector('.gmul-name');
        if (n) picked.push(n.textContent.trim());
      }}
    }});
    btn.textContent = '分组：' + (picked.length
      ? picked.slice(0, 2).join('、') + (picked.length > 2 ? ' 等 ' + picked.length + ' 组' : '')
      : '全部分组') + ' ▾';
  }}
  panel.querySelectorAll('input[type=checkbox]').forEach(function (b) {{
    b.addEventListener('change', refresh);
  }});
  refresh();
}})();
</script>
<div class="card" style="overflow:auto">
<table class="nowrap" id="contest-table"><tr><th>赛制</th><th>级别</th><th>名称</th><th>名单</th><th>已提交</th>
<th>状态</th><th>创建时间</th><th>操作</th></tr>
{"".join(rows) or ('<tr><td colspan="8" class="muted">没有符合条件的比赛（换个分组试试，或点「清空」）。</td></tr>' if filtering else '<tr><td colspan="8" class="muted">还没有比赛，点击「新建比赛」创建第一场。</td></tr>')}
</table>
</div>
{pagination}
<p class="muted">「发布成绩」= 学生立刻能在自己的页面（我的比赛 / 查成绩）看到自己的分数。
 所有赛制均在老师公布后可见；发布之后随时可以「收回成绩」。</p>
<p class="muted">要加题目？在「<a href="{admin_url(key, path='/admin/problems')}">题目列表</a> ·
 <a href="{admin_url(key, path='/admin/problem')}">新建题目</a>」里选整个出题文件夹（或 zip），
 系统会自动识别题面、测试数据、标程与大样例。</p>
"""
        self._send(page("管理端 · 比赛列表", body), cookie=cookie)

    def _admin_new_page(self, q: dict):
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        rules_desc = "".join(f'<li>{rule_badge({"rule": k})} {html.escape(v["name"])}：'
                             f'{html.escape(v["desc"])}</li>' for k, v in store.RULES.items())
        # 级别下拉：时长跟着级别给个默认值（J 210 / S 240），换级别时 JS 顺手改一下输入框
        level_opts = "".join(
            f'<option value="{lv}"{" selected" if lv == store.DEFAULT_LEVEL else ""}>'
            f'{html.escape(store.level_name(lv))}（考号 {store.DEFAULT_PREFIX}-{lv}·····，'
            f'默认 {store.LEVEL_MINUTES[lv]} 分钟）</option>'
            for lv in ("J", "S"))
        body = f"""
{self._admin_nav(key)}
<h2>新建比赛</h2>
<div class="card">
<form method="post" action="/admin/new{cid_query(key, "")}">
<div class="row">
  <span>比赛名称</span>
  <input type="text" name="title" placeholder="例如 2026 CSP-J 模拟赛" style="width:320px;max-width:100%">
  <span>赛制</span>
  <select name="rule" style="width:200px">
    {"".join(f'<option value="{k}">{html.escape(v["name"])}</option>' for k, v in store.RULES.items())}
  </select>
</div>
<div class="row" style="margin-top:10px">
  <span>级别</span>
  <select name="level" id="csp-new-level" style="width:280px;max-width:100%">{level_opts}</select>
  <span>比赛时长</span>
  <input type="text" name="duration" id="csp-new-duration"
         value="{store.LEVEL_MINUTES[store.DEFAULT_LEVEL]}" style="width:70px"> 分钟
</div>
<p class="muted" style="margin-top:10px">级别决定考号格式与默认时长：入门级
  <code>GD-J·····</code>、提高级 <code>GD-S·····</code>（5 位纯随机数，不是从 1 顺着排）。
  学生交完只看到「已提交」，成绩由老师点「公布成绩」后才可见。</p>
<ul class="muted">{rules_desc}</ul>
<p class="muted">创建后进入本场管理，继续设置名单、考号前缀和比赛题目。</p>
<p class="row" style="margin-top:10px"><button type="submit">创建比赛并继续设置</button>
<a class="btn btn-gray" href="{admin_url(key)}">返回比赛列表</a></p>
</form>
</div>
<script>
(function () {{
  var MINUTES = {json.dumps(store.LEVEL_MINUTES)};
  var sel = document.getElementById('csp-new-level');
  var dur = document.getElementById('csp-new-duration');
  if (!sel || !dur) return;
  sel.addEventListener('change', function () {{
    var d = MINUTES[sel.value];
    if (d) dur.value = d;              /* 换级别时把时长改成该级别的默认值，可以再手动改 */
  }});
}})();
</script>

"""
        self._send(page("管理端 · 新建比赛", body))

    def _admin_contest(self, q: dict, cookie: str = "", flash: str = ""):
        key = q.get("key", "")
        cid = q.get("c", "")
        contest = store.get_contest(cid)
        if not contest:
            self._admin_home(q, cookie)
            return
        exam = store.load_exam(cid)
        rule = store.rule_of(contest)
        roster = store.roster_detail(cid)
        results = store.load_results(cid)
        problems = []
        try:
            problems = localoj.list_problems()
        except hydro.HydroError as e:
            flash = self._flash("err", f"读取题目列表失败：{e}")

        # 本场名单表：**不显示评测站密码** —— 那是判分时系统自己登录学生账号用的
        # （见 core/grading.py 的 judge_csp → hydro.submit(uname, pw, …)），老师不需要看到
        rows = "".join(
            f'<tr><td>{html.escape(r["kaohao"])}</td><td>{html.escape(r["name"])}</td>'
            f'<td>{"已建" if r.get("uid") else "未建"}</td>'
            f'<td>{"已提交" if r.get("submitted") else "—"}</td></tr>' for r in roster)
        pending = sum(1 for r in roster if not r.get("uid"))
        # 这两个按钮原来各自是一个 <form>；现在名单表挪进第 2 节的卡片里（那张卡在
        # 整页唯一的「保存」表单里），**表单不能嵌套**，所以改用 formaction：
        # 按钮是保存表单的一部分，但点它时提交到另一个地址。两个处理函数都只读
        # URL 里的 key/c，多余的字段会被忽略。
        make_acct = (f'<p style="margin-top:10px">'
                     f'<button type="submit" class="btn-gray"'
                     f' formaction="/admin/accounts{cid_query(key, cid)}">'
                     f'提前建好本场 {pending} 个账号</button>'
                     f'<span class="muted"> 不点也没关系：学生第一次提交时会自动建'
                     f'（每个账号要启动一次评测站进程，所以后台串行创建，慢一点但不会拖垮服务器）</span>'
                     f'</p>') if pending else ''
        roster_table = (
            f'<h3 style="font-size:15px;margin:20px 0 6px">本场名单（{len(roster)} 人）'
            f'<span class="muted"> 考号随机分配，发下去之前建议点开「考号表」核对</span></h3>'
            f'<div style="overflow:auto">'
            f'<table><tr><th>考号</th><th>姓名</th><th>评测站账号</th><th>提交</th></tr>'
            f'{rows}</table></div>{make_acct}' if roster else
            '<p class="muted" style="margin-top:14px">本场还没有名单，用上面的分组或粘贴名单导入。</p>')
        group_options = "".join(
            f'<option value="{html.escape(g.get("gid", ""))}">'
            f'{html.escape(g.get("name", ""))}（{len(g.get("students") or [])} 人）</option>'
            for g in store.load_groups()) or '<option value="">（还没有分组，可先去「管理名单分组」新建）</option>'
        # 本场题目：老师看到「题目编号（T00001，系统分配、用来定位查找）」+「英文名
        # （学生侧，可编辑）」两列。英文名的默认值取题库里建题时填的那个；题库没填过
        # 就按本场顺序给 p1/p2…（保证学生侧永远有个合法的英文名，不会看到 T00001）。
        probs_now = []
        for p in exam.get("problems", []):
            pid = str(p.get("pid") or "")
            probs_now.append({
                "pid": pid,
                "number": store.number_of(p) or make_problem.code_of_pid(pid),
                "name": store.code_of(p),
                "title": str(p.get("title") or ""),
                "full": int(p.get("full") or 100),
            })
        entry_url = f"http://{self.headers.get('Host', '').split(':')[0]}:{PORT}/enter?c={urllib.parse.quote(cid)}"
        api_query = cid_query(key, cid)      # 选题下拉用的 /api/problems 地址（带密钥或靠 cookie）
        # 题库登记表：每道题的题目编号 + 建题时填的默认英文名
        # （选题候选表、已选题目表都要显示，而接口 /api/problems 只回 pid+标题）
        codes_map = make_problem.load_codes()
        pid_numbers = {str(k): str((v or {}).get("code") or "") for k, v in codes_map.items()}
        pid_names = {str(k): str((v or {}).get("name") or "") for k, v in codes_map.items()}
        # 题目表（顺序/编号/英文名/满分）由页面里的脚本渲染，这里把数据下发下去；
        # 隐藏字段 problems_json 的初始值就是**当前状态** —— 就算浏览器没跑 JS，
        # 点「保存」也不会把题目丢掉（老页面这一点踩过：没有 problems_json 会清空题目）。
        picked_json = _js_json(probs_now)
        pid_numbers_json = _js_json(pid_numbers)
        pid_names_json = _js_json(pid_names)
        problems_json_attr = html.escape(json.dumps(probs_now, ensure_ascii=False), quote=True)
        # 改级别等于重发考号：有人提交过就锁住级别（沿用 reshuffle 的那个保护）
        has_results = bool(results)
        level_lock = (' disabled title="本场已经有人提交，改级别会让成绩对错人"'
                      if has_results else "")
        level_now = store.level_of(contest)
        level_names = {"J": "入门级 J 组（考号 GD-J·····，3.5 小时）",
                       "S": "提高级 S 组（考号 GD-S·····，4 小时）"}
        level_opts = "".join(
            f'<option value="{lv}"{" selected" if lv == level_now else ""}>'
            f'{html.escape(level_names[lv])}</option>' for lv in ("J", "S"))

        body = f"""
{self._admin_nav(key, cid)}
{flash}
<p>{rule_badge(contest)} {level_badge(contest)}
   <b>{html.escape(contest["title"])}</b></p>
<p><b>学生入口</b>：<code>{entry_url}</code></p>
<div class="kv card">
  <div><b>名单</b>{len(roster)} 人</div>
  <div><b>已提交</b>{len(results)} 人</div>
  <div><b>提交开关</b>{"开放" if contest.get("open", True) else "已关闭"}</div>
  <div><b>成绩</b>{"已公布" if exam.get("released") else "未公布"}</div>
</div>
<p>
  <form method="post" action="/admin/release{cid_query(key, cid)}" style="display:inline">
    <input type="hidden" name="released" value="{0 if exam.get('released') else 1}">
    <button type="submit" class="{'btn-gray' if exam.get('released') else ''}">
      {"收回成绩公布" if exam.get("released") else "公布成绩"}</button></form>
  <form method="post" action="/admin/release{cid_query(key, cid)}" style="display:inline">
    <input type="hidden" name="open" value="{0 if contest.get('open', True) else 1}">
    <button type="submit" class="btn-gray">{"关闭提交" if contest.get("open", True) else "开放提交"}</button></form>
</p>

<!-- 整个页面只有底部一个「保存」按钮：本场设置 + 名单 + 题目 三个区域在同一个表单里，
     点一次一起提交。上面那两个按钮（公布成绩 / 关闭提交）是立即生效的动作开关，
     不是"保存"，所以留在表单外面（表单不能嵌套）。 -->
<form method="post" action="/admin/release{cid_query(key, cid)}" id="csp-save-all">
<input type="hidden" name="action" value="save_all">

<h2>1. 本场设置</h2>
<div class="card">
<div class="row">
  <span>级别</span>
  <select name="level" style="width:300px"{level_lock}>{level_opts}</select>
  <span>时长</span><input type="text" name="duration"
     value="{store.duration_of(contest)}" style="width:70px;display:inline-block"> 分钟
  <span>考号前缀</span><input type="text" name="prefix"
     value="{html.escape(contest.get("prefix", store.DEFAULT_PREFIX))}"
     style="width:70px;display:inline-block">
</div>
<p class="muted" style="margin-top:10px">{("本场已经有人提交，级别已锁住，要换级别请先删掉本场成绩"
   "（或新建一场考试）。" if has_results else "改级别等于重发本场考号（考号里的 J/S 跟着级别走）。")}</p>
</div>

<h2>2. 名单与考号（每场考试纯随机发号）</h2>
<div class="card">
<p><a class="btn btn-sm btn-gray" href="/admin/groups{cid_query(key, '')}">管理名单分组</a>
   <a class="btn btn-sm btn-gray" href="/admin/print{cid_query(key, cid)}" target="_blank">考号表（打印发给学生）</a>
</p>
<p style="margin-top:12px"><b>① 从分组导入</b>：</p>
<div class="row">
  <select name="gid" style="width:280px">
    <option value="">— 不用分组，用下面的粘贴名单 —</option>
    {group_options}
  </select>
</div>
<p style="margin-top:12px"><b>② 或者临时粘贴名单</b>（每行一个姓名，可从 Excel 整列粘贴；
   不用动就留空，保存时跳过）：</p>
<textarea name="names" placeholder="学生01&#10;学生02&#10;学生03" style="min-height:110px"></textarea>
<div class="row" style="margin-top:12px">
  <label><input type="radio" name="mode" value="append" checked style="width:auto"> 追加（已有学生保持原考号）</label>
  <label><input type="radio" name="mode" value="replace" style="width:auto"> 重建（清空后全部重发考号）</label>
</div>
<p class="muted" style="margin-top:10px">考号 = 前缀 + 级别字母 + 5 位随机数（如 <code>{html.escape(contest.get("prefix", store.DEFAULT_PREFIX))}-{
    store.level_of(contest)}48213</code>），纯随机、场内不重号、各场各发。
 导入后会为每个考号在评测站建好账号。</p>

{roster_table}
{f'''
<p style="margin-top:10px">
  <button type="submit" class="btn-gray" formaction="/admin/reshuffle{cid_query(key, cid)}"
          onclick="return confirm('把本场 {len(roster)} 个考号全部重新随机分配？学生不变，考号全换。')">
    重新随机分配考号</button>
  <span class="muted">只在还没人提交时可用（免得成绩对错人）</span>
</p>''' if roster and not results else ''}
</div>

<h2>3. 本场题目</h2>
<div class="card">
<p>搜题库 → 点「加入」；加进来的题会出现在<b>下面的「本场题目」</b>里，顺序就是第 1、2、3… 题。
  「英文名」是学生看到的名字（建文件夹、命名源文件、写 freopen 都用它），
  留空按顺序给 <code>p1</code>、<code>p2</code>…；只允许小写字母、数字、下划线，同场不能重名。
  题库里没有？<a href="{admin_url(key, path='/admin/problem')}">去新建一道 →</a></p>
<p class="csp-picker">
  <input type="text" id="csp-search" autocomplete="off" placeholder="输入标题搜索，例如 糖果分配 / A+B">
  <button type="button" class="btn-gray" id="csp-refresh">刷新题库</button>
</p>
<h3 style="font-size:15px;margin:14px 0 6px">题库待选</h3>
<div class="csp-catalog-wrap">
<table id="csp-catalog-table">
  <tr><th>题目编号</th><th>标题</th><th style="width:70px">加入</th></tr>
  <tbody id="csp-catalog-body"><tr><td colspan="3" class="muted">正在读取题库…</td></tr></tbody>
</table>
</div>
<p class="muted" id="csp-catalog-note" style="margin-top:6px"></p>
<h3 style="font-size:15px;margin:16px 0 6px">本场题目（按这个顺序发给学生）</h3>
<table id="csp-picked-table">
  <tr><th style="width:64px">顺序</th><th>题目编号</th><th>英文名（可编辑）</th><th>题目</th>
      <th>读写的文件</th><th style="width:80px">满分</th><th style="width:170px">操作</th></tr>
  <tbody id="csp-picked-body"></tbody>
</table>
<input type="hidden" name="problems_json" id="csp-problems-json" value='{problems_json_attr}'>
<details class="csp-manual">
  <summary>手动填写（读不到题库或想直接粘贴时用）</summary>
  <p class="muted">按顺序写题库标识，逗号分隔。这里的英文名写成 <code>G01:candy</code> 这种形式
  也可以（不写就按本场顺序给 <code>p1</code>、<code>p2</code>…）。
  <b>只要在这里动了内容，就以这里为准</b>（清空且点保存 = 清空本场题目）；没动过就用上面的「本场题目」表格里的。</p>
  <input type="text" name="pids"
       value="{html.escape(",".join(str(p.get("pid", "")) + (":" + store.code_of(p) if store.code_of(p) else "")
                                    for p in exam.get("problems", [])))}"
       placeholder="G01,G02" id="csp-pids">
</details>
<script>
(function () {{
  /* 本场题目表（顺序 | 题目编号 | 英文名 | 题目 | 读写的文件 | 满分 | 操作）由这段脚本渲染：
     排序/改英文名/改满分都在这里，提交时整表写进 problems_json。 */
  var current = {picked_json};
  var NUMBERS = {pid_numbers_json};      // 题库登记表：pid → 题目编号（T00001）
  var NAMES = {pid_names_json};          // 题库登记表：pid → 建题时填的默认英文名
  var catalog = [];
  var box = document.getElementById('csp-catalog-body');       /* 题库待选的表格 */
  var body = document.getElementById('csp-picked-body');       /* 本场题目的表格 */
  var note = document.getElementById('csp-catalog-note');      /* 「题库共 N 题」，跟着题库待选 */
  var search = document.getElementById('csp-search');
  var hidden = document.getElementById('csp-problems-json');
  var pids = document.getElementById('csp-pids');

  function titleOf(pid) {{
    var hit = catalog.filter(function (c) {{ return c.pid === pid; }})[0];
    return hit ? (hit.title || '') : '';
  }}
  function numberOf(pid) {{
    return NUMBERS[pid] || '（题库里还没登记编号，重新导入一遍这道题就会分配）';
  }}
  function defaultName(pid) {{
    return NAMES[pid] || '';
  }}
  function esc(s) {{
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}
  function openFileCell(name) {{
    return esc(name) ? esc(name) + '.in / ' + esc(name) + '.out' : '—';
  }}
  function render() {{
    if (!body) return;
    body.innerHTML = '';
    if (!current.length) {{
      body.innerHTML = '<tr><td colspan="7" class="muted">还没有选题：在上面的「题库待选」里点「加入」。</td></tr>';
      return;
    }}
    current.forEach(function (p, i) {{
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>第 ' + (i + 1) + ' 题</td>' +
        '<td><b>' + (p.number ? esc(p.number) : '<span class="muted">—</span>') + '</b></td>' +
        '<td><input type="text" class="csp-name" value="' + esc(p.name) + '" ' +
            'placeholder="如 candy" style="width:150px;padding:5px 8px;font-size:14px"></td>' +
        '<td>' + (esc(p.title) || esc(titleOf(p.pid)) || '（题库里没找到题目，可以点「刷新题库」重试）') + '</td>' +
        '<td class="csp-files">' + openFileCell(p.name) + '</td>' +
        '<td><input type="text" class="csp-full" value="' + esc(p.full) + '" ' +
            'style="width:60px;padding:5px 8px;font-size:14px"></td>' +
        '<td></td>';
      var nm = tr.querySelector('.csp-name');
      nm.addEventListener('input', function () {{
        current[i].name = nm.value;
        tr.querySelector('.csp-files').innerHTML = openFileCell(nm.value.trim());
        sync();
      }});
      var fl = tr.querySelector('.csp-full');
      fl.addEventListener('input', function () {{ current[i].full = fl.value; sync(); }});
      var ops = tr.lastChild;
      var up = document.createElement('button');
      up.type = 'button'; up.textContent = '↑'; up.className = 'btn-sm btn-gray';
      up.title = '上移';
      up.addEventListener('click', function () {{
        if (i > 0) {{ var x = current.splice(i, 1)[0]; current.splice(i - 1, 0, x); render(); sync(); }}
      }});
      var down = document.createElement('button');
      down.type = 'button'; down.textContent = '↓'; down.className = 'btn-sm btn-gray';
      down.title = '下移';
      down.addEventListener('click', function () {{
        if (i < current.length - 1) {{ var x = current.splice(i, 1)[0]; current.splice(i + 1, 0, x); render(); sync(); }}
      }});
      var del = document.createElement('button');
      del.type = 'button'; del.textContent = '移除'; del.className = 'btn-sm btn-gray';
      del.addEventListener('click', function () {{ current.splice(i, 1); render(); sync(); }});
      ops.appendChild(up); ops.appendChild(document.createTextNode(' '));
      ops.appendChild(down); ops.appendChild(document.createTextNode(' '));
      ops.appendChild(del);
      body.appendChild(tr);
    }});
  }}
  function sync() {{
    if (hidden) hidden.value = JSON.stringify(current);
  }}
  function add(pid) {{
    if (current.some(function (p) {{ return p.pid === pid; }})) return;
    var n = current.length + 1;
    /* 英文名默认取题库里建题时填的那个；没填过就按本场顺序给 p1、p2… */
    var name = defaultName(pid) || ('p' + n);
    current.push({{ pid: pid, number: NUMBERS[pid] || '', name: name,
                   title: titleOf(pid), full: 100 }});
    render(); sync();
  }}
  function showCatalog() {{
    if (!box) return;
    var q = (search && search.value || '').trim().toLowerCase();
    var hit = catalog.filter(function (c) {{
      return !q || (c.title || '').toLowerCase().indexOf(q) >= 0;
    }});
    box.innerHTML = '';
    if (!catalog.length) {{
      box.innerHTML = '<tr><td colspan="3" class="muted">题库是空的，或读不到题目' +
        '（可点「刷新题库」重试，或用下面的手动填写）。</td></tr>';
      return;
    }}
    if (!hit.length) {{
      box.innerHTML = '<tr><td colspan="3" class="muted">没找到匹配的题目。</td></tr>';
      return;
    }}
    hit.slice(0, 60).forEach(function (c) {{
      var tr = document.createElement('tr');
      var num = c.number || NUMBERS[c.pid] || '';
      var nm = c.name || NAMES[c.pid] || '';
      tr.innerHTML = '<td><b>' + (num ? esc(num) : '<span class="muted">—</span>') + '</b></td>' +
        '<td>' + (esc(c.title) || '(无标题)') +
        (nm ? ' <span class="muted">默认英文名 ' + esc(nm) + '</span>' : '') + '</td>' +
        '<td></td>';
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = '加入'; b.className = 'btn-sm';
      b.addEventListener('click', function () {{ add(c.pid); if (search) search.value = ''; showCatalog(); }});
      tr.lastChild.appendChild(b);
      box.appendChild(tr);
    }});
  }}
  function load(refresh) {{
    if (box) box.innerHTML = '<tr><td colspan="3" class="muted">正在读取题库…</td></tr>';
    var url = '/api/problems{api_query}' + (refresh ? (api_query ? '&' : '?') + 'refresh=1' : '');
    fetch(url).then(function (r) {{ return r.json(); }}).then(function (d) {{
      catalog = d.items || [];
      (catalog || []).forEach(function (c) {{
        if (c.number) NUMBERS[c.pid] = c.number;
        if (c.name) NAMES[c.pid] = c.name;
      }});
      if (note) {{
        note.textContent = d.error ? d.error
          : ('题库共 ' + catalog.length + ' 题（读取于 ' + (d.fetched_at || '') + '）');
      }}
      render();
      showCatalog();
    }}).catch(function () {{
      if (box) box.innerHTML = '<tr><td colspan="3" class="muted">读取题库失败，可用下面的手动填写。</td></tr>';
    }});
  }}
  if (search) search.addEventListener('input', showCatalog);
  var rf = document.getElementById('csp-refresh');
  if (rf) rf.addEventListener('click', function () {{ load(true); }});
  if (pids) pids.addEventListener('input', function () {{
    /* 老师手工改了「手动填写」→ 以它为准（把表格那份清掉，服务端就会走 pids） */
    if (hidden) hidden.value = '';
  }});
  render(); sync(); load(false);
}})();
</script>
</div>

<p style="margin-top:18px">
  <button type="submit" style="font-size:16px;padding:12px 30px">保存</button>
  <span class="muted">本场设置 + 名单导入 + 题目表一起保存。</span>
</p>
</form>

{GD_RULES}
"""
        self._send(page(f"管理端 · {contest['title']}", body), cookie=cookie)

    def _admin_new(self):
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        form = self._form()
        level = (form.get("level") or store.DEFAULT_LEVEL).strip().upper()
        if level not in store.LEVEL_MINUTES:
            level = store.DEFAULT_LEVEL
        try:
            duration = int(form.get("duration") or 0)
        except ValueError:
            duration = 0
        c = store.create_contest(form.get("title", ""), form.get("rule", store.DEFAULT_RULE),
                                 level=level, duration_min=duration or None)
        log(f"[管理端] 新建比赛 {c['id']} {c['title']}（{c['rule']}，{c['level']} 组，"
            f"{c['duration_min']} 分钟）")
        self._redirect(f"/admin{cid_query(q.get('key', ''), c['id'])}")

    def _admin_delete(self):
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        c = store.get_contest(cid)
        if c and store.delete_contest(cid):
            log(f"[管理端] 删除比赛 {cid} {c['title']}")
            self._redirect(admin_url(q.get('key', ''), msg=f"已删除比赛「{c['title']}」"))
        else:
            self._redirect(admin_url(q.get('key', ''), msg="删除失败：比赛不存在"))

    def _admin_roster(self):
        """导入名单（POST /admin/roster）：从分组导入、或临时粘贴姓名；**本场考号随机分配**。"""
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        if not store.get_contest(cid):
            self._redirect(admin_url(q.get("key", ""), msg="比赛不存在"))
            return
        msg, _changed = self._apply_roster(cid, self._form())
        self._redirect(admin_url(q.get("key", ""), cid, msg))

    def _apply_roster(self, cid: str, form: dict) -> tuple[str, bool]:
        """导入名单的**纯逻辑**（网页路由与底部统一的「保存」都调它）。

        返回 (给老师看的结果说明, 是否真的动了名单)。
        名单留空（没选分组、也没粘贴姓名）时不算错、也不改动 —— 这一条是给统一的
        「保存」按钮用的：老师只想改题目时，不该因为名单框是空的就报错。
        """
        contest = store.get_contest(cid) or {}
        mode = form.get("mode", "append")          # append（追加）| replace（重建本场）
        prefix = (form.get("prefix") or contest.get("prefix") or store.DEFAULT_PREFIX).strip().upper()

        gid = (form.get("gid") or "").strip()
        if gid:
            g = store.get_group(gid)
            if not g:
                return "这个分组不存在（可能已被删除）。", False
            names = list(g.get("students") or [])
            source = f"分组「{g['name']}」"
        else:
            names = [n for n in re.split(r"[\r\n,，、\t]+", form.get("names", "")) if n.strip()]
            source = "粘贴的名单"
        if not names:
            return "", False
        if mode == "replace":
            before = len(store.load_roster(cid))
            roster = store.replace_roster(cid, names, prefix=prefix)
            note = f"已按{source}重建本场名单（原有 {before} 人已清空），{len(roster)} 人全部重新随机分配考号。"
        else:
            before = len(store.load_roster(cid))
            roster = store.assign_kaohaos(cid, names, prefix=prefix)
            note = (f"已按{source}导入：本场现在 {len(roster)} 人"
                    f"（新增 {len(roster) - before} 人，考号随机分配）。")
        store.update_contest(cid, prefix=prefix)
        # 账号改成「学生第一次提交时自动建」（见 ensure_account）：
        # 建一个账号要启动一整套 Hydro 进程，一次导入给全班每人起一个会把机器压垮
        # （踩过：三场考试连导，2 核机器直接卡死）。要提前建就在本场管理里点按钮。
        fresh = sum(1 for v in roster.values() if not v.get("uid"))
        if fresh:
            note += (f" 其中 {fresh} 人的评测站账号会在各自第一次提交时自动创建"
                     "（想提前全部建好，点本场管理里的「提前建好账号」）。")
        return note, True

    def _create_accounts(self, cid: str, unames: list[str]):
        """后台给本场学生开评测站账号（限速串行，一个建完再建下一个）。"""
        # 判题不用评测站账号了（本地沙箱跑），所以这一步**没有事要做**：
        # 函数与接口保留，只是立刻返回 —— 老师的操作流程（点「建账号」）不用变，
        # 页面上也不会再冒出"账号建失败"这类跟判题无关的报错。
        log(f"[管理端] 本场 {cid} 无需建评测站账号（判题在本地沙箱跑）：{len(unames)} 个考号跳过")

    def _admin_accounts(self):
        """提前把本场名单的评测站账号都建好（后台限速执行，可以随时做别的事）。"""
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        if not store.get_contest(cid):
            self._redirect(admin_url(q.get("key", ""), msg="比赛不存在"))
            return
        todo = [v.get("uname") for v in store.load_roster(cid).values() if not v.get("uid")]
        if not todo:
            self._redirect(admin_url(q.get("key", ""), cid, "本场账号都建好了，不用再建。"))
            return
        self._create_accounts(cid, todo)
        self._redirect(admin_url(
            q.get("key", ""), cid,
            f"正在后台建 {len(todo)} 个账号（每个约几秒，串行执行，建完刷新页面能看到「已建」）。"))

    def _admin_reshuffle(self):
        """把本场考号重新随机分配（已有提交时拒绝，否则成绩会错位）。"""
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        contest = store.get_contest(cid)
        if not contest:
            self._redirect(admin_url(q.get("key", ""), msg="比赛不存在"))
            return
        if store.load_results(cid):
            self._redirect(admin_url(q.get("key", ""), cid,
                                     "本场已经有人提交，重新分配考号会让成绩对错人，已拒绝。"
                                     "要换考号请先删掉本场成绩（或新建一场考试）。"))
            return
        if not store.load_roster(cid):
            self._redirect(admin_url(q.get("key", ""), cid, "本场还没有名单。"))
            return
        roster = store.reshuffle_kaohaos(cid, prefix=contest.get("prefix", store.DEFAULT_PREFIX))
        self._redirect(admin_url(q.get("key", ""), cid,
                                 f"已重新随机分配 {len(roster)} 个考号（学生不变，考号全换）。"
                                 "新账号会在学生第一次提交时自动创建（也可以点「提前建好账号」）。"))

    def _admin_print(self, q: dict):
        """考号表：打印出来发给学生（含本场的专用考试链接 + 发给学生时的提醒清单）。"""
        if not self._check_admin(q.get("key", "")):
            self._admin_login()
            return
        cid = q.get("c", "")
        contest = store.get_contest(cid)
        if not contest:
            self._admin_home(q)
            return
        entries = sorted(store.load_roster(cid).items())
        entry_url = f"http://{self.headers.get('Host', '').split(':')[0]}:{PORT}/enter?c={urllib.parse.quote(cid)}"
        rows = "".join(
            f'<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(v.get("name", "?"))}</td></tr>'
            for k, v in entries)
        prefix = contest.get("prefix", store.DEFAULT_PREFIX)
        level = store.level_of(contest)
        minutes = store.duration_of(contest)
        hours = f"{minutes / 60:g} 小时"
        # 发给学生时的提醒清单（广东考区规则；文案与考区通告 notice_guangdong.md 对齐）
        # 本场的题目英文名（学生要用的文件夹名）——从 exam.json 里读，别写死举例
        gnames = [store.code_of(p) for p in store.load_exam(cid).get("problems", [])]
        gname_tip = ("、".join(f"<code>{html.escape(x)}</code>" for x in gnames[:3])
                     + ("…" if len(gnames) > 3 else "")) if gnames else "<code>candy</code>"
        tips = [f'考号是 <code>{html.escape(prefix)}-{level}</code> + 5 位随机数，'
                f'减号是<b>半角</b>，字母<b>全大写</b>，不能有空格',
                f'交卷目录：考号目录 → 题目英文名子文件夹 → 英文名.cpp'
                f'（本场的英文名：{gname_tip}）',
                '别忘了交个人信息文件（文件名＝学生自己的名字，内容含姓名/性别/年级/地区/学校/辅导老师/提交的程序）',
                '代码里用 freopen 读写本题英文名对应的 <code>.in</code> / <code>.out</code>，'
                '文件名与文件夹名严格区分大小写，<code>_</code> 与 <code>-</code> 不能混用',
                f'本场<b>赛中不显示任何判分信息</b>：交完只显示「已提交」，'
                f'成绩由老师在考试结束后公布']
        tip_html = "".join(f"<li>{t}</li>" for t in tips)
        body = f"""
    <p>{rule_badge(contest)} {level_badge(contest)}
       <b>{html.escape(contest["title"])}</b></p>
    <p><a class="btn btn-gray" href="/admin{cid_query(q.get('key', ''), cid)}">← 返回本场管理</a>
   <button onclick="window.print()">打印这张表</button></p>
<div class="card">
<p>考试入口（学生在浏览器里打开这个链接，输入自己的考号）：</p>
<p><code style="font-size:15px">{html.escape(entry_url)}</code></p>
<p class="muted">考号是本场随机生成的（5 位纯随机数），与其他场次无关；请勿把这张表公开张贴。
   本场时长 <b>{html.escape(hours)}</b>（{html.escape(store.level_name(level))}，{minutes} 分钟）。</p>
<table><tr><th>考号</th><th>姓名</th></tr>{rows or '<tr><td colspan=2 class="muted">本场还没有名单</td></tr>'}</table>
</div>

<p class="muted">这张表是给学生看的：一个人一行，剪开分发或贴在考场门口。点「打印这张表」直接出纸。</p>

<div class="rule-warn">
  <b>发给学生时一并提醒（广东考区规则）</b>
  <ul>{tip_html}</ul>
</div>"""
        self._send(page(f"考号表 · {contest['title']}", body))

    def _admin_groups(self, q: dict, flash: str = ""):
        """名单分组：一组学生可以反复用于不同考试（每场自动重新随机分配考号）。"""
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        groups = store.load_groups()
        cards = []
        for g in groups:
            names = g.get("students") or []
            preview = "、".join(names[:12]) + ("…" if len(names) > 12 else "")
            # 「已用于」里带上那场比赛的级别与模式徽章（同一个分组会反复用在 J 组/S 组）
            used = []
            for c in store.list_contests():
                if not (set(store.names_in_roster(c["id"])) & set(names)):
                    continue
                    used.append(f'{html.escape(c["title"])} {level_badge(c)}')
            cards.append(f"""
<div class="card">
<h2 style="margin-top:0">{html.escape(g.get("name", ""))}
  <span class="muted">共 {len(names)} 人</span></h2>
<p class="muted">{html.escape(preview) or "（空分组）"}</p>
{f'<p class="muted">已用于：{"；".join(used)}</p>' if used else ''}
<form method="post" action="{admin_url(key, path='/admin/groups')}" style="display:inline">
  <input type="hidden" name="action" value="update">
  <input type="hidden" name="gid" value="{html.escape(g.get('gid', ''))}">
  <input type="hidden" name="group_name" value="{html.escape(g.get('name', ''))}">
  <details style="margin:8px 0"><summary>编辑名单</summary>
    <textarea name="names" style="min-height:120px">{html.escape(chr(10).join(names))}</textarea>
    <p><button type="submit">保存名单</button></p>
  </details>
</form>
<form method="post" action="{admin_url(key, path='/admin/groups')}" style="display:inline"
      onsubmit="return confirm('删除分组「{html.escape(g.get('name', ''))}」？已导入到考试里的名单不受影响。')">
  <input type="hidden" name="action" value="delete">
  <input type="hidden" name="gid" value="{html.escape(g.get('gid', ''))}">
  <button type="submit" class="btn btn-sm btn-danger">删除分组</button>
</form>
</div>""")
        body = f"""
{self._flash("ok", flash) if flash else ""}
{self._admin_nav(key)}
<p><a class="btn btn-gray" href="/admin">← 比赛列表</a></p>
<h2>新建分组</h2>
<div class="card">
<form method="post" action="{admin_url(key, path='/admin/groups')}">
<input type="hidden" name="action" value="create">
<p>分组名称 <input type="text" name="group_name" placeholder="例如 初三1班 / 集训A班"
   style="width:280px;display:inline-block"></p>
<p style="margin-top:10px">学生姓名，每行一个（也可从 Excel 整列粘贴）：</p>
<textarea name="names" placeholder="学生01&#10;学生02&#10;学生03"></textarea>
<p style="margin-top:12px"><button type="submit">保存分组</button></p>
<p class="muted">分组只是"一批学生"，可以反复用在不同考试里；每场考试的考号会自动随机分配。</p>
</form>
</div>
<h2>已有分组（{len(groups)} 个）</h2>
{"".join(cards) or '<p class="muted">还没有分组。</p>'}
"""
        self._send(page("名单分组", body))

    def _admin_groups_post(self):
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        form = self._form()
        action = form.get("action", "")
        names = [n for n in re.split(r"[\r\n,，、\t]+", form.get("names", "")) if n.strip()]
        if action == "create":
            g = store.create_group(form.get("group_name", ""), names)
            dup = store.clean_names(names)[1]
            msg = f"已新建分组「{g['name']}」，{len(g['students'])} 人。"
            if dup:
                msg += f"（重复姓名已合并：{'、'.join(sorted(set(dup)))}）"
            self._redirect(admin_url(key, msg=msg, path="/admin/groups"))
            return
        if action == "update":
            g = store.update_group(form.get("gid", ""), name=form.get("group_name"),
                                   names=names)
            msg = f"分组「{(g or {}).get('name', '?')}」已更新，{len((g or {}).get('students') or [])} 人。"
            self._redirect(admin_url(key, msg=msg, path="/admin/groups"))
            return
        if action == "delete":
            ok = store.delete_group(form.get("gid", ""))
            self._redirect(admin_url(key, msg="分组已删除。" if ok else "分组不存在。",
                                     path="/admin/groups"))
            return
        self._redirect(admin_url(key, msg="未知操作。", path="/admin/groups"))

    def _admin_exam(self):
        """保存本场题目（POST /admin/exam）。单点保存用；底部统一的「保存」走 save_all。"""
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        if not store.get_contest(cid):
            # 少了 c= 会往 data/contests/ 下写出垃圾文件（踩过）
            self._redirect(admin_url(q.get("key", ""), msg="没有指定比赛，请从比赛列表进入。"))
            return
        msg, _changed = self._apply_exam(cid, self._form())
        self._redirect(admin_url(q.get('key', ''), cid, msg))

    def _parse_picked(self, form: dict) -> list[dict]:
        """把表单里的选题解析成 [{"pid", "name", "full"}]（顺序即题号）。

        两种来源：
          * `problems_json`（表格那份：带 `name` 英文名与每题 `full`）——“candy” 旧字段名
            `slug` 也当英文名认（老页面的写法）
          * `pids`（手动填写：“题库标识,题库标识”，也兼容 `题库标识:英文名` 的老写法）
        """
        picked: list[dict] = []
        raw_json = (form.get("problems_json") or "").strip()
        if raw_json:
            for it in json.loads(raw_json):
                if isinstance(it, dict):
                    pid = str(it.get("pid", "")).strip()
                    name = str(it.get("name") or it.get("slug") or "").strip()
                    full = it.get("full")
                else:
                    pid, name, full = str(it).strip(), "", None
                if pid:
                    picked.append({"pid": pid, "name": name, "full": full})
            return picked
        if (form.get("pids") or "").strip():
            for token in re.split(r"[,，\s]+", form.get("pids", "")):
                head, _, tail = token.partition(":")
                pid = head.strip()
                if pid:
                    picked.append({"pid": pid, "name": tail.strip(), "full": None})
        return picked

    def _apply_exam(self, cid: str, form: dict) -> tuple[str, bool]:
        """保存本场题目的**纯逻辑**（网页路由与底部统一的「保存」都调它）。

        数据模型（每道题）：
          * `number` / `code` —— **题目编号**（`T00001`，老师侧）。取自题库登记表；
            题库里还没登记的老题在这里补分配（`assign_number`），保证每道题都有编号。
          * `name` —— **英文名**（学生侧，如 `candy`）。老师填的优先；没填就取题库里
            建题时填的默认英文名；都没有就按本场顺序给 `p1`、`p2`…
            （学生侧永远有个合法英文名，绝不会看到 `T00001`。）
          * `full` —— 每题满分。

        返回 (给老师看的结果说明, 是否改动了题目)。
        """
        full_default = 100
        try:
            full_default = int(form.get("full") or 100)
        except (TypeError, ValueError):
            pass
        try:
            picked = self._parse_picked(form)
        except (ValueError, TypeError, AttributeError) as e:
            return f"选题数据格式不对（{e}），没有改动本场题目。", False
        exam = store.load_exam(cid)
        old = {str(p.get("pid")): p for p in exam.get("problems", [])}
        titles = {}
        try:
            titles = {str(p["pid"]): p["title"] for p in localoj.list_problems()}
        except hydro.HydroError:
            pass

        problems, notes, new_numbers, used_names = [], [], [], set()
        for i, one in enumerate(picked, 1):
            pid = one["pid"]
            prev = old.get(pid) or {}
            title = titles.get(pid) or str(prev.get("title") or "")
            # ---- 题目编号（老师侧）：题库登记表为准；没登记（或还是老值 T1/candy）就现在补/升级
            number = make_problem.code_of_pid(pid)
            if not store.is_problem_number(number):
                try:
                    number = make_problem.assign_number(pid, title=title)
                    new_numbers.append(number)
                except make_problem.CodeError as e:
                    notes.append(f"{pid} 分不到题目编号（{e}）")
                    number = ""
            # 每道题在 exam.json 里就两个身份字段（与迁移脚本写的一模一样）：
            #   * `code` —— **题目编号**（`T00005`，老师侧；`store.number_of()` 读它）
            #   * `name` —— **英文名**（`candy`，学生侧；`store.code_of()` 读它）
            item = {"no": i, "pid": pid, "title": title, "code": number, "full": full_default}
            try:
                if one.get("full") not in (None, ""):
                    item["full"] = int(one["full"])
            except (TypeError, ValueError):
                notes.append(f"第 {i} 题的满分「{one['full']}」不是数字，按 {full_default} 算")
            # ---- 英文名（学生侧），按这个顺序定：
            #   ① 老师没改（提交的正是学生当前看到的名字）→ 原样保留，不校验、不改名
            #      （老场次没英文名时学生看到的是 T1/T2/T3，保存一次不能把它变掉）
            #   ② 老师填的 → 本场上次存的名字 → 题库里建题时填的默认值 → 按本场顺序 p{n}
            #   （"本场上次存的"排在题库默认值前面：老师改过的名字不能被"重新保存"冲掉；
            #    老格式的 POST 不带名字时也靠它保住原有英文名）
            want = str(one.get("name") or "").strip()
            prev_name = str(prev.get("name") or "").strip()
            cur_name = store.code_of(prev) if prev else ""
            bank_name = str(make_problem.name_of_pid(pid) or "").strip()
            if want and want == cur_name and not store.is_problem_number(want):
                pass                                   # ① 没改
            else:
                if not want:
                    want = prev_name or bank_name or f"p{i}"
                if not store.valid_code(want):
                    fallback = (prev_name or bank_name
                                or (cur_name if not store.is_problem_number(cur_name) else "")
                                or f"p{i}")
                    if not store.valid_code(fallback):
                        fallback = f"p{i}"
                    notes.append(f"第 {i} 题的英文名「{want}」不合法"
                                 f"（只能用小写字母、数字、下划线），已改回 {fallback}")
                    want = fallback
            if want in used_names:      # 同一场里不能重名（重名 = 同一个文件夹）
                base, k = want, 2
                while f"{base}_{k}" in used_names:
                    k += 1
                notes.append(f"第 {i} 题的英文名「{want}」和前面某题重了，已改成 {base}_{k}")
                want = f"{base}_{k}"
            used_names.add(want)
            item["name"] = want
            problems.append(item)

        exam["problems"] = problems
        store.save_exam(cid, exam)
        if not problems:
            return "题目已清空（本场现在没有题目）。", True
        msg = f"已保存 {len(problems)} 道题：" + "、".join(
            f"第 {p['no']} 题 {p['name']}（{p['code'] or '无编号'}）" for p in problems[:6])
        if len(problems) > 6:
            msg += "…"
        if new_numbers:
            msg += f"；其中 {len(new_numbers)} 道题是刚补分配的题目编号"
        if notes:
            msg += "；" + "；".join(notes[:3])
        return msg, True

    def _admin_release(self):
        """本场属性开关：发布/收回成绩、开放/关闭提交；也承接本场管理页的几个表单。

        三个入口合成这一条路由（都在改"本场属性"，少一条 POST 路由就少动一次
        `web/server.py`）：
          * `action=save_all`  —— 本场管理页底部**唯一那个「保存」按钮**：
                                 本场设置 + 名单导入 + 题目表 一次全提交
          * `action=settings`  —— 只存「本场设置」（级别/时长/考号前缀）
          * 带 `released`/`open` —— 发布/收回成绩、开放/关闭提交（立即生效的动作开关）
        """
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        form = self._form()
        exam = store.load_exam(cid)
        contest = store.get_contest(cid) or {}
        if not contest:
            self._redirect(admin_url(q.get("key", ""), msg="比赛不存在"))
            return
        if form.get("action") == "save_all":
            self._admin_save_all(cid, contest, form, q.get("key", ""))
            return
        if form.get("action") == "settings":
            self._redirect(admin_url(q.get("key", ""), cid, self._apply_settings(cid, contest, form)))
            return
        if "released" in form:
            exam["released"] = form["released"] == "1"
            store.save_exam(cid, exam)
            store.update_contest(cid, released=exam["released"])
        if "open" in form:
            contest["open"] = form["open"] == "1"
            store.update_contest(cid, open=contest["open"])
        self._redirect(admin_url(q.get('key', ''), cid, "已更新。"))

    def _admin_save_all(self, cid: str, contest: dict, form: dict, key: str) -> None:
        """本场管理页底部那个唯一的「保存」：把整页的改动一次落盘。

        顺序有讲究：**先本场设置**（改级别/前缀会连同重发考号），**再名单导入**
        （新学生用刚设好的前缀与级别发号），**最后题目表**。
        每一步都返回一句人话，拼成一条明确的成功提示 flash 在页面顶部。
        """
        notes = []
        note = self._apply_settings(cid, contest, form)
        if note:
            notes.append(note)
        # 名单：没选分组、也没粘贴姓名 = 这次不动名单（不报错）
        msg, changed = self._apply_roster(cid, form)
        if changed:
            notes.append(msg)
        msg, changed = self._apply_exam(cid, form)
        if changed:
            notes.append(msg)
        log(f"[管理端] 本场管理保存 {cid}：" + "；".join(notes))
        tip = "；".join(notes) if notes else "没有需要保存的改动"
        self._redirect(admin_url(key, cid, "保存成功 —— " + tip))

    def _apply_settings(self, cid: str, contest: dict, form: dict) -> str:
        """保存「本场设置」：级别、时长、考号前缀。返回一句结果说明。

        **级别**决定考号里的 J/S 字母：改级别等于重发本场考号（原型上就是这么写的），
        所以有人提交之后直接拒绝（沿用 `_admin_reshuffle` 的保护——否则成绩会对错人）；
        没人提交时改级别会顺手重发一遍考号，免得场次级别与考号对不上。
        时长/前缀随时可改：前缀只影响以后新发的号。

        没提交 `level`（页面上被人提交过时会 disabled，浏览器就不发这个字段）时保持原级别。
        """
        notes = []
        old_level = store.level_of(contest)
        level = (form.get("level") or old_level).strip().upper()
        if level not in store.LEVEL_MINUTES:
            level = old_level
        if level != old_level:
            if store.load_results(cid):
                return ("本场已经有人提交，改级别会让考号与成绩对错人，已拒绝（其余改动照常保存）。"
                        "要换级别请先删掉本场成绩（或新建一场考试）。")
            store.update_contest(cid, level=level)
            # 级别写在考号里（GD-J… / GD-S…），所以改了级别就得重发考号 —— 这正是
            # 原型上写的「改级别等于重发考号」。有人提交过的话上面已经拦住了，这里可以放心重发。
            if store.load_roster(cid):
                prefix = (form.get("prefix") or contest.get("prefix") or "").strip().upper()
                roster = store.reshuffle_kaohaos(cid, prefix=prefix or None, level=level)
                notes.append(f"级别改为 {store.level_name(level)}，已重新随机分配 {len(roster)} 个考号")
            else:
                notes.append(f"级别已改为 {store.level_name(level)}")
        try:
            duration = int(form.get("duration") or 0)
        except ValueError:
            duration = 0
        if duration > 0:
            store.update_contest(cid, duration_min=duration)
            notes.append(f"时长 {duration} 分钟")
        prefix = (form.get("prefix") or "").strip().upper()
        if prefix:
            store.update_contest(cid, prefix=prefix)
            notes.append(f"考号前缀 {prefix}")
        if not notes:
            return ""
        log(f"[管理端] 本场设置 {cid}：" + "；".join(notes))
        return "本场设置（" + "；".join(notes) + "）"

    def _admin_scores(self, q: dict):
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        cid = q.get("c", "")
        contest = store.get_contest(cid)
        if not contest:
            self._admin_home(q)
            return
        exam = store.load_exam(cid)
        rule = store.rule_of(contest)
        rows = store.ranking(cid, include_all=True)
        probs = exam.get("problems", [])
        head = "".join(
            f'<th>{html.escape(store.slug_of(p))}'
            f'<span class="muted">/{p.get("full", 100)}</span></th>' for p in probs)
        body_rows = []
        # 一行占满整张表的空表提示（列数 = 名次/姓名/考号 + 每题 + 总分/提交时间）
        n_col = 6 + len(probs)
        for r in rows:
            cells = []
            for p in probs:
                key_ = store.problem_dir_name(int(p["no"]))
                got = r["problems"].get(key_) or {}
                full = int(p.get("full", 100))
                if not got:
                    # 区分"交了但 0 分"和"压根没交"
                    cells.append('<td class="muted">未交</td>')
                    continue
                score = int(got.get("score") or 0)
                st = graded_text(got, full)
                cls = "ok" if score >= full else ("part" if score > 0 else "err")
                # 点分数进详情页，看这一题的完整提交结果与代码
                link = (f'<a class="{cls}" href="/admin/student{cid_query(key, cid, kaohao=r["kaohao"])}'
                        f'#{key_}">{score}</a>')
                cells.append(f'<td>{link}'
                             f'<span class="muted"> {html.escape(st)}</span></td>')
            who = (f'<a href="/admin/student{cid_query(key, cid, kaohao=r["kaohao"])}">'
                   f'{html.escape(r["kaohao"])}</a>')
            body_rows.append(
                f'<tr><td>{r["rank"]}</td><td>{html.escape(r["name"])}</td>'
                f'<td>{who}</td>{"".join(cells)}'
                f'<td><b>{r["total"]}</b></td><td class="muted">{html.escape(r["at"])}</td></tr>')
        missing = [k for k in store.load_roster(cid) if k not in store.load_results(cid)]
        miss_html = ("<p class='warn'>还没有提交的学生：" +
                     "、".join(f'{html.escape(store.student_name(cid, k))}（{k}）' for k in missing) +
                     "</p>") if missing else ""
        # 一行占满整张表的空表提示（列数 = 名次/姓名/考号 + 每题 + 总分/提交时间）
        empty_row = f"<tr><td colspan='{n_col}' class='muted'>还没有任何提交</td></tr>"
        body = f"""
{self._admin_nav(key, cid)}
<p>{rule_badge(contest)} {level_badge(contest)}
   <b>{html.escape(contest["title"])}</b>
 · {"成绩已公布" if exam.get("released") else "成绩未公布"}</p>
<p class="muted">每题按<b>测试点</b>给分：通过几个点就拿几个点的分（每点满分 = 每题满分 ÷ 测试点数），
 不再是「子任务里有一个点没过就整段 0 分」。点某题分数进该学生的「提交详情」，
 里面有逐点明细，点每行的状态还能看这一点的输入、学生输出和标准答案。</p>
<div class="card" style="overflow:auto">
<table class="nowrap"><tr><th>名次</th><th>姓名</th><th>考号</th>{head}<th>总分</th><th>提交时间</th></tr>
 {"".join(body_rows) or empty_row}</table>
</div>
{miss_html}"""
        self._send(page(f"成绩总表 · {contest['title']}", body))
    def _admin_file(self, q: dict):
        """管理员看学生提交的某个文件（预览/下载）。"""
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        cid = q.get("c", "")
        kaohao = (q.get("k", "") or "").strip().upper()
        if not store.get_contest(cid) or not kaohao:
            self._redirect(admin_url(key))
            return
        back = f"/admin/student{cid_query(key, cid, kaohao=kaohao)}"
        self._serve_submit_file(cid, kaohao, q.get("f", ""),
                                download=bool(q.get("dl")), back=back, from_admin=True)

    def _admin_student(self, q: dict):
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        cid = q.get("c", "")
        kaohao = (q.get("k", "") or "").strip().upper()
        contest = store.get_contest(cid)
        if not contest:
            self._admin_home(q)
            return
        exam = store.load_exam(cid)
        rule = store.rule_of(contest)
        entry = (store.load_results(cid) or {}).get(kaohao)
        info = store.load_roster(cid).get(kaohao) or {}
        # 名次表**只取一次**：名次和下面的「快速换人」下拉都用它
        # （`include_all=True` = 连没交的学生一起列，顺序与「成绩总表」完全一致；
        #   加进来的都是 0 分、排在最后，已交学生的名次不受影响）
        ranked = store.ranking(cid, include_all=True)
        rank = next((r["rank"] for r in ranked if r["kaohao"] == kaohao), "—")
        back = f'/admin/scores{cid_query(key, cid)}'
        # 快速换人：这一页一次只看一个学生，老师常常要连着看好几个（查完一个看下一个），
        # 给个下拉直接跳，省得每次都回「成绩总表」再点进来。顺序就是成绩总表的顺序
        # （总分从高到低），选项里带着名次/姓名/考号/总分，找人也方便；
        # 没交的也列出来并标注（点进去会提示"还没有提交"）。
        picker = ""
        if len(ranked) > 1:
            opts, pos = [], 0
            for i, r in enumerate(ranked, 1):
                kh = str(r.get("kaohao") or "")
                if kh == kaohao:
                    pos = i
                opts.append(
                    f'<option value="{html.escape(kh, quote=True)}"'
                    + (" selected" if kh == kaohao else "")
                    + f'>第 {r["rank"]} 名 · {html.escape(str(r.get("name") or "?"))}'
                      f'（{html.escape(kh)}）· {r["total"]} 分'
                    + ("" if r.get("submitted") else " · 未交")
                    + '</option>')
            picker = (
                '<form method="get" action="/admin/student" class="row" '
                'style="margin:8px 0;align-items:center;gap:8px">'
                f'<input type="hidden" name="key" value="{html.escape(key, quote=True)}">'
                f'<input type="hidden" name="c" value="{html.escape(cid, quote=True)}">'
                '<span>快速换人</span>'
                '<select name="k" onchange="this.form.submit()" style="min-width:320px">'
                + "".join(opts) +
                '</select>'
                '<noscript><button type="submit">看这位</button></noscript>'
                f'<span class="muted">第 {pos} / {len(ranked)} 个</span>'
                '</form>')
        head = (f'{self._admin_nav(key, cid)}'
                f'<p><a class="btn btn-gray" href="{back}">← 返回成绩总表</a></p>'
                + picker +
                f'<h1>{html.escape(info.get("name", "?"))}'
                f'<span class="muted"> · {html.escape(kaohao)}</span></h1>'
                f'<p>{rule_badge(contest)} {level_badge(contest)}'
                    f' <b>{html.escape(contest["title"])}</b></p>')
        if not entry:
            self._send(page(f"提交详情 · {kaohao}",
                            head + self._flash("info", "这位学生还没有提交。")), 404)
            return

        # 概要
        no_sub = ""
        if not entry.get("problems"):
            no_sub = self._flash("info", "这位学生一次有效提交都没有（成绩表里的 0 分是缺席，不是答错）。")
        head += (f'<div class="card"><div class="kv">'
                 f'<div><b>总分</b>{store.entry_total(entry)} 分</div>'
                 f'<div><b>名次</b>第 {rank} 名</div>'
                 f'<div><b>提交时间</b>{html.escape(entry.get("submitted_at", ""))}</div>'
                     f'<div><b>状态</b>{"成绩已公布" if exam.get("released") else "成绩未公布"}</div>'
                 f'</div></div>{no_sub}')

        # 学生实际传上来的目录结构（老师排查"为什么这题 0 分"第一眼要看的就是这个）
        picked_note = {}
        for p in exam.get("problems", []):
            got = (entry.get("problems") or {}).get(store.problem_dir_name(int(p["no"]))) or {}
            if got.get("file"):
                picked_note[got["file"]] = f"第 {p['no']} 题（{store.code_of(p)}）"
        # 判分还没跑完时（刚上传），用上传时记下的"每题用了哪个文件"补上
        for no_s, rel in (entry.get("picked") or entry.get("structure_picked") or {}).items():
            if rel in picked_note:
                continue
            prob = [p for p in exam.get("problems", []) if str(p.get("no")) == str(no_s)]
            if prob:
                picked_note[rel] = f"第 {prob[0]['no']} 题（{store.code_of(prob[0])}）"
        base = store.upload_dir(cid, kaohao)
        file_link = (lambda rel: "/admin/file" + cid_query(key, cid, kaohao=kaohao)
                     + "&f=" + urllib.parse.quote(rel))
        # 广东考区要求交个人信息文件（文件名＝本人姓名，不参与判分）：
        # 在目录树里标出这一行，没交就在树上直接给红框提示（老师一眼看到缺什么）。
        rel_files = []
        for _root, _dirs, _fns in os.walk(base):
            _dirs[:] = [d for d in _dirs if d != "__pycache__"]
            for _fn in _fns:
                rel_files.append(os.path.relpath(os.path.join(_root, _fn), base).replace("\\", "/"))
        person_rel = ""
        if rel_files and info.get("name"):
            person_rel = wrapper.find_person_file(rel_files, info.get("name"),
                                                  strict=True)
        if person_rel:
            picked_note[person_rel] = "个人信息文件（不参与判分）"
        tree_text, n_files, n_bytes = render_upload_tree_html(base, picked_note, file_link)
        if tree_text:
            legend = ("　← 第 N 题 = 这一题判分用的代码；← 没用到 = 不是任何一题要的文件"
                      + ("；← 个人信息文件 = 广东考区要求交的那个" if person_rel else ""))
            person_html = ""
            if not rel_files:
                pass                      # 一个文件都没有：下面的"没匹配到代码"已经说清了
            elif person_rel:
                person_html = (f'<p class="ok">个人信息文件已交：<code>{html.escape(person_rel)}</code>'
                               f'（文件名＝学生姓名，不参与判分）</p>')
            else:
                want = f"{info.get('name')}.txt"
                person_html = (f'<div class="warn">没找到个人信息文件 '
                               f'<code>{html.escape(want)}</code>（广东考区要求必须有，'
                               f'文件名＝学生姓名，内容含姓名/性别/年级/地区/学校/辅导老师/提交的程序）。</div>')
            head += (f'<div class="card"><h2 style="margin-top:0">学生上传的目录结构'
                     f'<span class="muted"> 共 {n_files} 个文件，合计 {_fmt_bytes(n_bytes)}'
                     f' · 点文件名即可查看内容</span></h2>'
                     f'{tree_text}'
                     f'<p class="muted">{legend}；括号里是文件大小，'
                     f'原始文件在服务器 <code>{html.escape(os.path.join(base, ""))}</code></p>'
                     f'{person_html}</div>')
        miss = entry.get("missing_sources") or []
        if miss:
            head += ('<div class="card"><h2 style="margin-top:0">没匹配到代码的题目</h2>'
                     + "".join(f'<div class="warn">{html.escape(m)}</div>' for m in miss) + "</div>")

        # 逐题明细
        blocks = []
        for p in exam.get("problems", []):
            no = int(p["no"])
            code = store.code_of(p)
            full = int(p.get("full", 100))
            pk = store.problem_dir_name(no)
            got = (entry.get("problems") or {}).get(pk) or {}
            rows = [f'<div class="kv"><div><b>得分</b>{got.get("score", 0)} / {full} 分</div>'
                    f'<div><b>判分结果</b>'
                    f'{graded_cell(got, full, show_score=False) if got else "<span class=muted>未提交</span>"}'
                    f'{"（评测机状态：" + html.escape(got.get("status_text", "")) + "）" if got else ""}</div>'
                    f'<div><b>提交次数</b>{got.get("tries") or "—"}</div>']
            if got:
                rows.append(f'<div><b>用时/内存</b>{_fmt_ms(got.get("time"))} / {_fmt_kb(got.get("memory"))}</div>')
                att = got.get("attempt_status_text") or ""
                if att and att != got.get("status_text"):
                    # 注意：服务器是 Python 3.10，f-string 表达式里不能再用同类引号，字典先建好
                    att_item = {"score": got.get("attempt_score"),
                                "status": got.get("attempt_status"),
                                "status_text": att}
                    rows.append(f'<div><b>最近一次</b>{graded_cell(att_item, full, show_score=False)}'
                                f'（{got.get("attempt_score", 0)} 分，{html.escape(got.get("attempt_at", ""))}）</div>')
                if got.get("hint"):
                    rows.append(f'<div><b>提交检查</b>{html.escape(str(got["hint"]))}</div>')
            rows.append('</div>')

            # 测试点明细：三层（状态条 → 得分构成 → 逐点明细表）
            cases = got.get("testcases") or []
            tp_html = _tp_block(cases, full, no)
            if got and not cases:
                # 没有逐点数据：编译错误给报错原文（不要说成"未运行"），其它给一句解释
                st = int(got.get("status") or 0)
                if st == 7:
                    err_text = got.get("compile_error") or ""
                    tp_html = ('<h3 style="font-size:15px;margin:16px 0 6px">测试点明细</h3>'
                               '<div class="tp-card tp-fail">'
                               '<b>编译没通过，这道题的测试点一个都没跑。</b>'
                               '<p class="muted" style="margin:6px 0 0">'
                               '编译错误要先解决编译问题，再谈得分——这类 0 分和算法无关。</p></div>'
                               + ('<details open><summary>编译器的报错原文</summary>'
                                  '<pre class="code-view">' + html.escape(err_text) + '</pre></details>'
                                  if err_text else
                                  '<p class="muted">（评测机没有回传编译器报错原文，'
                                  '下面「查看提交的代码」里可以直接看代码。）</p>'))
                elif st in hydro.STATUS_PENDING:
                    tp_html = ('<h3 style="font-size:15px;margin:16px 0 6px">测试点明细</h3>'
                               '<div class="tp-card"><b>还在评测中…</b>'
                               '<p class="muted" style="margin:6px 0 0">刷新一下页面就能看到逐点结果。</p></div>')
                else:
                    tp_html = ('<h3 style="font-size:15px;margin:16px 0 6px">测试点明细</h3>'
                               '<div class="tp-card"><b>这一题没有逐点数据。</b>'
                               '<p class="muted" style="margin:6px 0 0">'
                               + html.escape(str(got.get("note") or got.get("status_text") or ""))
                               + '（老记录、没有评测成功、或这一点的数据太旧没留存，'
                                 '判定与用时见上面的「判分结果」。）</p></div>')

            # 学生提交的原始代码（注意：这里不能再用 code 这个名字，上面那个是题目编号）
            src_html = ""
            rel = got.get("file") or ""
            if rel:
                path = os.path.join(store.upload_dir(cid, kaohao), rel)
                if os.path.isfile(path):
                    try:
                        src_text = open(path, encoding="utf-8", errors="replace").read()
                    except OSError:
                        src_text = ""
                    src_html = ('<details><summary>查看提交的代码（'
                                f'{html.escape(rel)}）</summary>'
                                '<pre class="code-view">' + html.escape(src_text) + '</pre></details>')
                else:
                    src_html = ('<p class="muted">源码文件没有留存（记录里写的是 '
                                f'{html.escape(rel)}）——这份成绩来自早期数据迁移，'
                                '那时提交的代码没有按目录结构保存。</p>')
            # 题目编号（T00001，老师定位用）与英文名（学生看到的、判分用的文件夹名）都标出来
            code_no = store.number_of(p)
            blocks.append(
                f'<div class="card" id="{pk}"><h2>第 {no} 题 · '
                f'<code>{html.escape(code)}</code>'
                + (f'<span class="muted"> {html.escape(code_no)}</span>' if code_no else "")
                + f'<span class="muted"> {html.escape(str(p.get("title") or p.get("pid") or ""))}</span></h2>'
                + "".join(rows) + tp_html + src_html + "</div>")

        # 测试点详情弹窗的取数地址（c/k 固定，n=第几题、t=第几个点由页面 JS 补）
        tp_query = cid_query(key, cid, kaohao=kaohao)
        script = (f'<script>window.CSP_TP_QUERY = {json.dumps(tp_query)};</script>' + TP_JS)
        self._send(page(f"提交详情 · {kaohao}",
                        head + "".join(blocks) + _tp_detail_modal() + script))

    # =================================================================
    # 题目列表（新页）/admin/problems：题库全貌 + 查看题面 + 自己测试
    # =================================================================

    def _catalog_rows(self) -> tuple[list[dict], str]:
        """题库清单（题目列表页用）：编号 / 标题 / 测试点 / 时限内存 / 大样例。

        数据来自三处，各补一块：
          * `store.load_catalog()`  —— 题库里有哪些题（评测站）
          * `data/problem_codes.json`（`make_problem.load_codes()`）—— 系统分配的题目编号
          * `data/problem_info.json`（本文件维护）—— 建题时记下的测试点数/时限/内存/标程
        评测站读不到时（网络/容器没起来）退回编号登记表里已知的题目，页面不至于空白。
        """
        cat = store.load_catalog()
        items = cat.get("items") or []
        err = ""
        if not items:
            try:
                items = localoj.list_problems()
                store.save_catalog(items)
            except (hydro.HydroError, OSError) as e:
                err = f"读取评测站题库失败：{e}"
        codes = make_problem.load_codes()
        info = load_problem_info()
        titles = {str(it.get("pid", "")): str(it.get("title", "")) for it in items}
        known = list(titles)
        for pid in list(codes) + list(info):
            if pid not in titles:
                known.append(pid)
                titles.setdefault(pid, str((info.get(pid) or {}).get("title") or ""))
        rows = []
        for pid in known:
            rec = info.get(pid) or {}
            samples = make_problem.load_samples(pid) if make_problem else {}
            s_items = samples.get("items") or []
            rows.append({
                "pid": pid,
                "title": titles.get(pid) or rec.get("title") or "(无标题)",
                "code": make_problem.code_of_pid(pid, codes) or rec.get("code") or "",
                # 建题时填的默认英文名（加进比赛时会用它当输入框的默认值）
                "name": make_problem.name_of_pid(pid, codes) or rec.get("name") or "",
                "cases": int(rec.get("cases") or 0),
                "time_ms": int(rec.get("time_ms") or 0),
                "memory_mb": int(rec.get("memory_mb") or 0),
                "sample_groups": len(s_items),
                "std": str(rec.get("std") or ""),
                # 假删除的记号（`deleted`）：列表页据此把它收进「已删除的题目」那一块。
                # 页面上不过滤掉，两个列表都要用。
                "deleted": bool(rec.get("deleted")),
                "deleted_at": str(rec.get("deleted_at") or ""),
            })
        return rows, err

    def _statement_html(self, pid: str, cache_dir: str, fetch: bool) -> str:
        """题面 → HTML（服务端渲染，一份 Markdown 逻辑，见 core/markdown.py）。

        本地缓存（`data/statements/<pid>.md`）有就直接渲染；没有的话**只对场上正在用
        的题目**去评测站取一次（每取一次都是一次数据库查询，题库大了不能都取）。
        """
        text = ""
        path = os.path.join(cache_dir, f"{pid}.md")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = ""
        if not text.strip() and fetch:
            try:
                text = localoj.problem_statement(pid, cache_dir)
            except Exception as e:                   # noqa: BLE001（评测站闹脾气不该让整页打不开）
                log(f"[题目列表] 取题面 {pid} 失败：{e}")
                text = ""
        return md_to_html(text) if text.strip() else ""

    def _catalog_view(self) -> tuple[list, list, list, dict, str]:
        """题库的一次完整视图：`(全部, 在用, 已假删除, 用在本场的, 错误信息)`。

        「用在本场」是 `{pid: [「第 1 题·CSP 模拟赛」, …]}`。

        列表页、删除/恢复的回包、题目详情页都用这一份 —— 口径必须一致，
        否则会出现"点了删除、列表换了一份、一刷新又变回去"这种对不上的事。
        """
        rows_all, err = self._catalog_rows()
        used: dict[str, list[str]] = {}
        for c in store.list_contests():
            for p in store.load_exam(c["id"]).get("problems", []):
                used.setdefault(str(p.get("pid") or ""), []).append(
                    f'第 {p.get("no")} 题·{c["title"]}')
        return (rows_all,
                [r for r in rows_all if not r.get("deleted")],
                [r for r in rows_all if r.get("deleted")],
                used, err)

    def _problem_row_html(self, key: str, r: dict, used: dict) -> str:
        """「全部题目」里的一行（含操作列的三个按钮，一行排开）。

        操作列从四个按钮减到三个：「查看题面」现在**跳到题目详情页**
        （左边改、右边看，比弹窗看一眼睛信息多得多），「改题面」并进那一页
        （同一件事不留两个入口），剩下「自己测试」「删除」。
        """
        pid = r["pid"]
        esc = lambda s: html.escape(str(s), quote=True)             # noqa: E731
        code = r["code"]
        cases_cell = str(r["cases"]) if r["cases"] else '<span class="muted">—</span>'
        if r["time_ms"] and r["memory_mb"]:
            tl_text = (f"{r['time_ms'] / 1000:g} 秒 / {r['memory_mb']} MB"
                       if r["time_ms"] % 1000 == 0 else
                       f"{r['time_ms']} 毫秒 / {r['memory_mb']} MB")
            tl_hover = ""
        else:
            # 这一格以前写「—（没记下，自己测试时看用时）」，太长把表格顶宽了；
            # 提示挪到 title 里，格子本身只留一个短横。
            tl_text = "—"
            tl_hover = "建题时没记下，点「自己测试」跑一遍就知道实际用时"
        sample_cell = (f'<span class="ok">有</span> <span class="muted">{r["sample_groups"]} 组</span>'
                       if r["sample_groups"] else '<span class="muted">—</span>')
        use_note = ("　<span class=\"muted\">用在本场：" + "、".join(used[pid][:2]) + "</span>"
                    if used.get(pid) else "")
        # 表格不出现横向滚动条，靠 CSS 按列限宽 + 省略号截断；截掉的部分写在 title 里，
        # 鼠标悬停能看全（老师要认题，不能把标题截得看不出来是哪道）。
        title_txt = str(r["title"] or "")
        hover = " · ".join(x for x in (title_txt, use_note and
                                       "用在本场：" + "、".join(used[pid])) if x)
        ops = (f'<div class="prob-ops">'
               f'<a class="btn btn-sm btn-gray" href="{problem_detail_url(key, pid)}">查看题面</a>'
               f'<button type="button" class="btn btn-sm" data-test="{esc(pid)}">自己测试</button>'
               f'<button type="button" class="btn btn-sm btn-danger" data-del="{esc(pid)}" '
               f'data-title="{esc(title_txt or pid)}">删除</button>'
               f'</div>')
        return (f'<tr><td><b>{html.escape(code) if code else "—"}</b></td>'
                f'<td title="{esc(r["name"])}"><code>{html.escape(r["name"])}</code></td>'
                f'<td title="{esc(hover)}">{html.escape(title_txt)}{use_note}</td>'
                f'<td>{cases_cell}</td>'
                f'<td class="muted" title="{esc(tl_hover)}">{tl_text}</td>'
                f'<td>{sample_cell}</td>'
                f'<td>{ops}</td></tr>')

    def _deleted_row_html(self, key: str, g: dict) -> str:
        """「已删除的题目」里的一行（看题面 / 恢复 / 彻底删除）。"""
        esc = lambda s: html.escape(str(s), quote=True)             # noqa: E731
        ops = (f'<div class="prob-ops">'
               f'<a class="btn btn-sm btn-gray" href="{problem_detail_url(key, g["pid"])}">查看题面</a>'
               f'<button type="button" class="btn btn-sm" data-restore="{esc(g["pid"])}">恢复</button>'
               f'<button type="button" class="btn btn-sm btn-danger" data-purge="{esc(g["pid"])}" '
               f'data-title="{esc(g["title"] or g["pid"])}">彻底删除</button>'
               f'</div>')
        return (f'<tr><td><b>{html.escape(str(g["code"]) or "—")}</b></td>'
                f'<td class="muted">{html.escape(str(g["name"]))}</td>'
                f'<td>{html.escape(str(g["title"]))}</td>'
                f'<td class="muted">{html.escape(g["deleted_at"] or "")}</td>'
                f'<td>{ops}</td></tr>')

    def _deleted_block_html(self, key: str, gone: list, open_: bool = False) -> str:
        """「已删除的题目」那一整块（`<details>` 连同表格）。

        平时收起（`<details>`），有内容才出现；假删除的题仍然能看题面。
        `open_=True` 给删除/恢复的 AJAX 回包用：刚删完就得让人看见它去哪了，
        不然页面上"少了一行"会以为是删没了。
        """
        if not gone:
            return ('<div id="prob-del" class="muted" style="margin-top:14px">'
                    '（没有已删除的题目）</div>')
        rows = "".join(self._deleted_row_html(key, g) for g in gone)
        return (
            f'<details id="prob-del" style="margin-top:14px"' + (" open" if open_ else "") + '>'
            f'<summary style="cursor:pointer"><b>已删除的题目（{len(gone)} 道）</b>'
            f'<span class="muted"> —— 只是从上面的列表里收起来了：题面、历史提交记录都还在，'
            f'可以恢复或彻底删除。<b>这些题不再影响建题</b>：同一个标识再建一次会建出一份新的'
            f'（内部换个标识，两份互不影响）</span></summary>'
            f'<div class="card" style="overflow:auto;margin-top:8px">'
            f'<table><tr><th>题目编号</th><th>英文名</th><th>标题</th><th>删除时间</th><th>操作</th></tr>'
            f'<tbody id="prob-del-rows">{rows}</tbody></table></div></details>')

    def _finish_problem_action(self, key: str, msg: str, *, ok: bool = True) -> None:
        """删除 / 恢复 / 彻底删除的收尾：网页表单跳转，前端 fetch 拿 JSON。

        走 JSON 时**把两张表的 HTML 一起回给前端**（行还是服务端渲染的，只有一处逻辑），
        前端只换表格内容：页面不跳、滚动位置不丢 —— 老师点了删除还站在原地看着那一行消失，
        而不是被弹回页面顶部（这一版专门改的就是这个）。
        """
        if not getattr(self, "_want_json", False):
            self._redirect(admin_url(key, path="/admin/problems", msg=msg))
            return
        _, rows, gone, used, _err = self._catalog_view()
        self._json({
            "ok": ok, "message": msg, "error": "" if ok else msg,
            "rows": "".join(self._problem_row_html(key, r, used) for r in rows),
            "deleted_html": self._deleted_block_html(key, gone, open_=True),
            "live_count": len(rows), "gone_count": len(gone),
        })

    def _admin_problems(self, q: dict):
        """题目列表（新页）：题库里全部题目 + 查看题面 + 自己测试。

        原型：`原型/admin/problems.html`。老师在这里确认「数据/时限/内存设置对不对」，
        不用先加进比赛再试。
        """
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        rows_all, rows, gone, used, err = self._catalog_view()
        # 题面**不在页面加载时取**（题库几十道题时，一次查几十条数据库太慢）：
        # 「查看题面」现在直接跳到题目详情页，题面在那页里读（一页只读一道题）。
        # `cand` 给页面里的「自己测试」弹窗用（标程要跟着弹窗预填）。
        cand = {}
        for r in rows_all:
            cand[r["pid"]] = {"code": r["code"], "name": r["name"], "title": r["title"],
                              "cases": r["cases"], "full": 100, "std": r["std"]}
        table_rows = [self._problem_row_html(key, r, used) for r in rows]
        # 「已删除的题目」那一块：平时收起（`<details>`），有内容才出现。
        # 假删除的题仍然能看题面/自己测试（它们只是不在上面那张表里）。
        gone_html = self._deleted_block_html(key, gone)
        data = _js_json(cand)
        body = f"""
{self._admin_nav(key)}
<p class="muted">题库里的全部题目，一行一道。<b>题目编号</b>（<code>T00001</code>）是系统分配的，
 老师只用它定位这道题；<b>英文名</b>是学生在比赛里看到的名字（加进比赛时可以按场次再改）。
 点「查看题面」进这道题的<b>详情页</b>（左边 Markdown 原文可以直接改，右边就是学生看到的样子，
 同一页还能看标程）；点「自己测试」拿代码跑一遍全部测试点，确认数据与时限设置没问题。
 删除是**从列表里收起来**（题面、提交记录都留着），删完页面停在原地，不跳回顶部。</p>
{"<p class='warn'>" + html.escape(err) + "（下面这份清单来自考试服务自己的记录）</p>" if err else ""}
<p><a class="btn" href="{admin_url(key, path='/admin/problem')}">+ 新建题目</a>
   <button type="button" class="btn btn-gray" id="csp-refresh-cat">刷新题库清单</button>
   <span class="muted" id="csp-refresh-note"></span></p>

<h2>全部题目（<span id="prob-live-count">{len(rows)}</span> 道）</h2>
<div id="prob-flash"></div>
<div class="card">
<table class="csp-prob-table">
<tr><th style="width:7%">题目编号</th><th style="width:9%">英文名</th><th>标题</th>
    <th style="width:6%">测试点</th><th style="width:9%">时限 / 内存</th>
    <th style="width:6%">大样例</th><th style="width:23%">操作</th></tr>
<tbody id="prob-rows">
{"".join(table_rows) or '<tr><td colspan="7" class="muted">题库还是空的：点上面的「新建题目」，'
                        '选整个出题文件夹（或 zip）就能自动识别建题。</td></tr>'}
</tbody>
</table>
</div>

{gone_html}
<div class="me-card">
  <b>题目编号是老师用的，英文名才是学生看到的</b><br>
  <b>题目编号</b>（<code>T00001</code>）建题时由系统分配、跟着题走，老师不能改，也不用记 ——
  只用它定位查找（「T00001 那道题」）。<br>
  <b>英文名</b>（如 <code>candy</code>）在<a href="{admin_url(key, path='/admin/problem')}">新建题目</a>时
  可以填一个当默认值，也可以留空；<b>加进某场比赛时</b>会自动填进那场比赛的「英文名」列，
  老师在那时可以按场次改（留空按本场顺序给 <code>p1</code>、<code>p2</code>…）。
  学生用它建文件夹、命名源文件、写 <code>freopen</code>。
</div>

<p class="muted">「测试点 / 时限 / 内存」是建题时记下来的；更早建的题没有这份记录（显示「—」），
 跑一次「自己测试」就知道实际有多少个点、最慢的点占了多少时限。</p>
{_test_modal()}
<script>window.CSP_CAND = {data};</script>
<script>window.CSP_KEY_QUERY = {json.dumps(cid_query(key, ""))};</script>
{_math_js()}
{PROBLEMS_JS.replace("{scan_url}", "/admin/scan")}
<script>
(function () {{
  var btn = document.getElementById('csp-refresh-cat');
  if (!btn) return;
  btn.addEventListener('click', function () {{
    var note = document.getElementById('csp-refresh-note');
    note.textContent = '　正在读评测站题库…';
    fetch('/api/problems' + (window.CSP_KEY_QUERY || '') +
          ((window.CSP_KEY_QUERY || '') ? '&' : '?') + 'refresh=1')
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        note.textContent = d.ok ? '　已刷新，正在重载页面…' : ('　' + (d.error || '刷新失败'));
        if (d.ok) location.reload();
      }})
      .catch(function (e) {{ note.textContent = '　刷新失败：' + e; }});
  }});
}})();
</script>
"""
        self._send(page("题目列表", body, math=True))

    def _admin_problem_detail(self, q: dict, flash: str = "", flash_kind: str = "ok"):
        """题目详情页：**左边改、右边看**（左边 Markdown 原文，右边学生看到的成品）。

        原型参考学生题面页 + 老「改题面」页；一页把"这道题到底是什么样"讲清楚，
        不用在三个页面之间来回跳：

          * 左边 —— 题目名 / 英文名 / 时限 / 内存 / 题面（Markdown，直接改）
          * 右边 —— 学生视角渲染出来的题面（改一个字右边立刻跟着变）
          * 下面 —— 标程（只读，可一键复制）、测试点数、大样例、用在哪儿场

        顶部三个按钮「左右对照 / 只看编辑 / 只看成品」：小屏或者只想专心改题面时用，
        选择记在浏览器里（localStorage），刷新后还是那个视图。
        """
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        esc = html.escape
        back = (f'<p><a class="btn btn-gray" href="{admin_url(key, path="/admin/problems")}">'
                f'← 题目列表</a></p>')
        pid = (q.get("pid") or "").strip()
        if not pid:
            self._send(page("题目详情", self._admin_nav(key) + back +
                            self._flash("err", "链接里没有 pid：不知道该看哪道题。")), 404)
            return
        rows_all, _rows, _gone, used, _err = self._catalog_view()
        rec = next((r for r in rows_all if r["pid"] == pid), None)
        if rec is None:
            self._send(page("题目详情", self._admin_nav(key) + back +
                            self._flash("err", f"题库里没有「{pid}」这道题。")), 404)
            return
        cache_dir = os.path.join(store.DATA_DIR, "statements")
        text = localoj.problem_statement(pid) or self._statement_raw(pid, cache_dir)
        title = str(rec.get("title") or pid)
        code = str(rec.get("code") or "")
        name = str(rec.get("name") or "")
        cases = int(rec.get("cases") or 0)
        groups = int(rec.get("sample_groups") or 0)
        time_ms = int(rec.get("time_ms") or 0)
        memory_mb = int(rec.get("memory_mb") or 0)
        std = str(rec.get("std") or "")
        used_txt = "、".join(used.get(pid) or []) or "还没用在任何一场比赛里"
        std_html = (code_pre(std) if std else
                    '<p class="muted">这道题没有存标程（建题时文件夹里没放 <code>标程.cpp</code>）。</p>')
        render_html = (md_to_html(text) if text.strip()
                       else '<p class="muted">（题面还是空的）</p>')
        # 覆盖重传的地址（这个表单在 `pd-form` **外面** —— HTML 表单不能嵌套）
        up_url = ("/admin/problem-reupload" + cid_query(key, "") +
                  ("&" if cid_query(key, "") else "?") + "pid=" + urllib.parse.quote(pid))
        limit_txt = (f"{time_ms / 1000:g} 秒 / {memory_mb} MB"
                     if time_ms and memory_mb else "—")
        kv = "".join(
            f'<div><b>{esc(k)}</b>{v}</div>' for k, v in [
                ("题目编号", f'<code>{esc(code) or "—"}</code>'),
                ("题库标识", f'<code>{esc(pid)}</code>'),
                ("英文名", f'<code>{esc(name)}</code>' if name else '<span class="muted">没设</span>'),
                ("测试点", f'{cases} 个' if cases else '<span class="muted">—</span>'),
                ("大样例", f'{groups} 组' if groups else '<span class="muted">—</span>'),
                ("时限 / 内存", esc(limit_txt)),
                ("用在本场", esc(used_txt)),
            ])
        body = f"""
{self._admin_nav(key)}
{back}
<h2 style="margin:6px 0 2px">{esc(title)}</h2>
<p class="muted" style="margin:0 0 6px">题目编号 <b>{esc(code) or "—"}</b>
   · 题库标识 <code>{esc(pid)}</code>
   {"· 英文名 <code>" + esc(name) + "</code>" if name else ""}
   · {esc(used_txt)}</p>
{self._flash(flash_kind, flash) if flash else ""}

<div class="pd-bar">
  <span class="muted">视图：</span>
  <button type="button" class="btn btn-sm btn-gray" data-pd-mode="both">左右对照</button>
  <button type="button" class="btn btn-sm btn-gray" data-pd-mode="left">只看编辑</button>
  <button type="button" class="btn btn-sm btn-gray" data-pd-mode="right">只看成品</button>
  <button type="submit" form="pd-form" class="btn" style="margin-left:auto">保存修改</button>
  <span class="muted" id="pd-note"></span>
</div>

<form id="pd-form" method="post" action="{problem_detail_url(key, pid)}">
<input type="hidden" name="pid" value="{esc(pid)}">
<div class="pd-wrap" id="pd-wrap" data-mode="both">
  <div class="pd-left">
    <div class="card">
      <label>题目名（列表、成绩、比赛里显示的名字）</label>
      <input type="text" name="title" value="{esc(title)}">
      <label>英文名（学生看到的，如 <code>candy</code>；可以留空）</label>
      <input type="text" name="name" value="{esc(name)}">
      <div class="row">
        <div style="flex:0 0 150px"><label>时限（毫秒）</label>
          <input type="number" name="time_ms" min="100" step="100" value="{time_ms or ''}"></div>
        <div style="flex:0 0 150px"><label>内存（MB）</label>
          <input type="number" name="memory_mb" min="16" step="16" value="{memory_mb or ''}"></div>
      </div>
      <p class="muted" style="font-size:13px;margin:8px 0 2px">
        时限 / 内存<b>真的会改变判题</b>（下一次提交就按新值跑）；英文名只在「这场比赛没单独设过」时生效。</p>
      <label>题面（Markdown —— 左边写完，右边立刻就是学生看到的样子）</label>
      <textarea class="codebox" id="pd-src" name="statement" spellcheck="false">{esc(text)}</textarea>
      <p class="row" style="margin:10px 0 0">
        <button type="submit" class="btn">保存修改</button>
        <span class="muted">Ctrl+S 也能存 · Tab 缩进 4 格 · 保存后学生端立刻生效</span>
      </p>
    </div>
    <div class="card">
      <details {"open" if std else ""}><summary style="cursor:pointer">
        <b>标程</b> <span class="muted">（只读；右上角「复制」可一键拿走）</span></summary>
        {std_html}</details>
      <details style="margin-top:8px"><summary style="cursor:pointer">
        <b>这道题的信息</b></summary><div class="kv" style="margin-top:8px">{kv}</div></details>
    </div>
  </div>
  <div class="pd-right">
    <div class="card">
      <div class="stmt" id="pd-render">{render_html}</div>
    </div>
  </div>
</div>
</form>
<p class="muted">上面右边那块就是学生在考试页点开题面看到的样子（公式会真的渲染出来）。
 保存只动题面与这几个字段 —— <b>测试数据、标程不变</b>。</p>

<div class="card">
  <h3 style="margin-top:0">重新上传题目文件夹（覆盖这道题）</h3>
  <p class="muted">规范与「新建题目」完全一样：文件夹名写成 <code>分类号-题名</code>
    （<b>题名取文件夹名</b>，和建题一致），里面放 <code>题目.md</code>（学生看到的题面）、
    <code>标程.cpp</code>、<code>data/01.in</code> + <code>01.out</code>（成对）、<code>大样例/</code>。
    传完这道题<b>按文件夹重做一遍</b>：题名、题面、标程、测试数据、大样例都换成文件夹里的；
    <b>题目编号不变</b>（现在是 <code>{html.escape(code) or "—"}</code>），
    <b>英文名 / 时限 / 内存保持原值</b>。立刻生效 —— 学生端题面、判题用的数据都是新的一份。</p>
  <form method="post" action="{up_url}" enctype="multipart/form-data" id="pd-up">
    <input type="file" name="folder" id="pd-up-files" webkitdirectory directory multiple>
    <p style="margin:10px 0 0">
      <button type="submit" class="btn btn-danger" id="pd-up-go">上传并覆盖</button>
      <span class="muted" id="pd-up-note">　还没有选文件夹（点左边的「选择文件」选整道题的文件夹）</span>
    </p>
  </form>
</div>
<script>
(function () {{
  var inp = document.getElementById('pd-up-files');
  var note = document.getElementById('pd-up-note');
  var form = document.getElementById('pd-up');
  if (!inp || !note || !form) return;
  inp.addEventListener('change', function () {{
    var fs = this.files || [];
    var top = fs.length ? String(fs[0].webkitRelativePath || '').split('/')[0] : '';
    note.textContent = fs.length
      ? ('　选好了：' + top + '（' + fs.length + ' 个文件）')
      : '　还没有选文件夹';
  }});
  form.addEventListener('submit', function (e) {{
    if (!inp.files || !inp.files.length) {{
      e.preventDefault();
      note.textContent = '　先选一个题目文件夹再点上传';
      return;
    }}
    if (!confirm('会用这个文件夹覆盖当前题目：题面、标程、测试数据、大样例都换成文件夹里的。\\n'
                 + '题目编号不变，英文名 / 时限 / 内存也不变。确定上传？')) e.preventDefault();
  }});
}})();
</script>
<script>window.CSP_KEY_QUERY = {json.dumps(cid_query(key, ""))};</script>
{PROBLEM_DETAIL_JS}
"""
        self._send(page(f"题目详情 · {title}", body, math=True))
    def _admin_problem_reupload(self):
        """重传整个出题文件夹，**覆盖**已有题目（题目详情页那个入口）。

        与「新建题目」同一套规范（`题目.md` / `标程.cpp` / `data/01.in|out` / `大样例/`），
        只差两点：

          * 表单带 `pid` → 覆盖**这道**题，**题目编号不变**（编号跟着题走，
            `assign_number()` 对已有编号是沿用的）；
          * 覆盖是这次的目的，所以 `create_problem(..., overwrite=True)`
            （新建题目那边仍然是"不覆盖、自动让位"，免得误撞已有的题）。

        文件夹会换掉的：题名（取 `题目.md` 的一级标题）、题面、标程、测试数据、大样例。
        不会被文件夹动的：**英文名**（`problem_info` 里那个默认名）、**时限 / 内存**
        —— 文件夹里没有这两样，所以把原值传回去，免得被默认的 1000 / 256 悄悄冲掉。
        """
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        pid = (q.get("pid") or "").strip()
        if not pid:
            self._redirect(admin_url(key, path="/admin/problems", msg="没有指定要覆盖哪道题。"))
            return
        if make_problem is None:
            self._redirect(problem_detail_url(key, pid, msg="服务端缺少建题模块，覆盖不了。"))
            return
        try:
            fields, files, file_fields = self._parse_multipart(MAX_PS_UPLOAD)
        except ValueError as e:
            self._redirect(problem_detail_url(key, pid, msg=f"上传失败：{e}"))
            return
        # 认**表单字段名**（folder / data），别认文件名 —— 中文文件名经 multipart 传输
        # 可能被编码搞乱（建题那边踩过：标程被当成测试数据丢掉）
        uploads = {fn: files[fn] for fn, field in file_fields.items() if field in ("folder", "data")}
        if not uploads:
            self._redirect(problem_detail_url(key, pid, msg=(
                "没有收到文件：请选**整个出题文件夹**（里面有 题目.md、标程.cpp、data/、大样例/），"
                "或者选成对的 .in/.out。")))
            return
        std = ""
        for fn, field in file_fields.items():
            if field == "std":
                std = files[fn].decode("utf-8", "replace")
        old = load_problem_info().get(pid) or {}
        old_code = str(old.get("code") or "") or make_problem.code_of_pid(pid) or ""
        # 注意：`time_ms` / `memory_mb` 传**原值**（没有就退回默认）—— 文件夹里没有这两样，
        # 不传的话 `create_problem` 会用默认值把它们改掉（老师没提时限却被改了，很坏）。
        rep = make_problem.create_problem(
            pid, "", uploads, overwrite=True,
            time_ms=int(old.get("time_ms") or 0) or make_problem.DEFAULT_TIME_MS,
            memory_mb=int(old.get("memory_mb") or 0) or make_problem.DEFAULT_MEMORY_MB,
            std_source=std)
        if not rep.get("ok"):
            log(f"[管理端] 覆盖重传 {pid} 失败：{rep.get('error')}")
            extra = "；" + "，".join(str(x) for x in (rep.get("problems") or [])[:3]) \
                if rep.get("problems") else ""
            self._redirect(problem_detail_url(key, pid, msg=(
                f"覆盖失败：{rep.get('error', '未知错误')}{extra}（这道题没有改动）")))
            return
        try:
            store.save_catalog(localoj.list_problems())
        except (hydro.HydroError, OSError) as e:                 # noqa: BLE001
            log(f"[管理端] 覆盖重传 {pid}：刷新题库缓存失败 {e!r}")
        # 元信息也要按新的记一遍（题目名 / 测试点数 / 标程）——「题目列表」和题目详情页
        # 里的"测试点 N 个、标程、题目名"读的都是 `problem_info.json`，
        # 只在建题那条路上记过；覆盖重传不记的话，列表里还显示旧的题名与点数（踩过）。
        remember_problem(pid, title=str(rep.get("title") or ""),
                         code=str(rep.get("number") or ""), cases=rep.get("cases"),
                         time_ms=int(old.get("time_ms") or 0) or None,
                         memory_mb=int(old.get("memory_mb") or 0) or None,
                         std=std)
        # 题面字数**读刚落盘的那份**：`create_problem` 的报告里没有题面字段，
        # 拿它报"0 字"会让老师以为题面没传上（踩过）
        try:
            stmt_len = len(localoj.problem_statement(pid) or "")
        except Exception:                                        # noqa: BLE001
            stmt_len = 0
        n_std = len(std)
        log(f"[管理端] 覆盖重传 {pid}：{rep.get('cases')} 组数据、题面 {stmt_len} 字、标程 {n_std} 字")
        self._redirect(problem_detail_url(key, pid, msg=(
            f"已用文件夹覆盖重传（{rep.get('title') or pid}）："
            f"{rep.get('cases')} 组测试数据、题面 {stmt_len} 字"
            + (f"、标程 {n_std} 字" if n_std else "")
            + f"；题目编号仍是 {old_code or '（没变）'}，英文名 / 时限 / 内存保持原值。"
            f"学生端题面和判题数据**立刻**是新的一份。")))

    def _admin_problem_detail_post(self):
        """保存题目详情页的改动：**题面 + 题目名 + 英文名 + 时限 + 内存**。

        分两处落盘：题面写 `data/statements/<pid>.md`（`localoj.set_problem_statement`），
        题目名/英文名/时限/内存写编号登记与题目信息（`remember_meta`）。
        任何一处失败都**如实报出来**（绝不说"保存好了"）—— 老师要靠这句话判断能不能走开。
        """
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        form = self._form()
        pid = (q.get("pid") or form.get("pid") or "").strip()
        if not pid:
            self._redirect(admin_url(key, path="/admin/problems", msg="没有指定要改哪道题。"))
            return
        title = (form.get("title") or "").strip()
        name = (form.get("name") or "").strip()
        statement = form.get("statement") or ""
        r = localoj.set_problem_statement(pid, statement)
        if not r.get("ok"):
            log(f"[管理端] 题目详情：保存题面失败 {pid}：{r.get('error')}")
            self._redirect(problem_detail_url(key, pid, msg=(
                f"题面没保存：{r.get('error')}。"
                f"（题目名 / 时限这些也一起没动，改完再存一次就行。）")))
            return
        m = remember_meta(pid, title=title, name=name,
                          time_ms=form.get("time_ms"), memory_mb=form.get("memory_mb"))
        changed = [x for x in (m.get("changed") or []) if x]
        skipped = [x for x in (m.get("skipped") or []) if x]
        log(f"[管理端] 题目详情保存 {pid}（{title or pid}）：题面 {len(statement)} 字"
            + (f"，{ '、'.join(changed) }" if changed else "，其余字段没变")
            + (f"；没写的：{ '、'.join(skipped) }" if skipped else "")
            + ("" if m.get("ok") else f"（元信息没写成功：{m.get('error')}）"))
        if not m.get("ok"):
            self._redirect(problem_detail_url(key, pid, msg=(
                f"题面存好了，但题目名 / 时限这些没写成功：{m.get('error')}。")))
            return
        parts = [f"保存好了（{title or pid}）：题面已更新"]
        if changed:
            parts.append("，" + "、".join(changed) + "也改了")
        parts.append("。")
        parts.append("；".join(skipped) + "。" if skipped else "学生端立刻是新的。")
        self._redirect(problem_detail_url(key, pid, msg="".join(parts)))

    def _api_testcase(self, q: dict):
        """取某个测试点的详情（给提交详情页的悬浮窗用）。

        `GET /admin/testcase?c=&k=&n=<第几题>&t=<点号>` → JSON。
        数据来自 `results.json` 里每题落盘的 `testcases`（见 core/grading.py）。
        """
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        cid = q.get("c", "")
        kaohao = (q.get("k", "") or "").strip().upper()
        pno = str(q.get("n", "") or "")
        want = str(q.get("t", "") or "").strip()
        exam = store.load_exam(cid)
        prob = next((p for p in exam.get("problems", []) if str(p.get("no")) == pno), None)
        entry = (store.load_results(cid) or {}).get(kaohao) or {}
        if not prob or not entry:
            self._json({"ok": False, "error": "找不到这一题或这位学生的成绩"}, 404)
            return
        got = (entry.get("problems") or {}).get(store.problem_dir_name(int(prob["no"]))) or {}
        full = int(prob.get("full", 100))
        cases = got.get("testcases") or []
        case = next((c for c in cases if str(c.get("no", "")).zfill(2) == want.zfill(2)), None)
        if case is None:
            err = "这一点的逐点数据没有留存"
            if int(got.get("status") or 0) == 7:
                err = "这一题是编译错误，没有逐点数据（报错原文见提交详情页）"
            self._json({"ok": False, "error": err}, 404)
            return
        _sym, cls = _tp_mark(case.get("status"))
        title = str(prob.get("title") or "")
        c_in = case.get("input") or ""
        c_out = case.get("output") or ""
        c_ans = case.get("answer") or ""
        # 评测记录里只有判定/用时/内存（Hydro 不存内容）。这时**输入和标准答案**
        # 还能从题目的数据文件里补出来（1.in / 1.out，与测试点 id 一一对应）；
        # 学生输出评测机比完就丢，补不回来——页面上会写明「评测机没有保存」。
        if not (c_in or c_ans):
            try:
                # 落盘的逐点数据里序号叫 `no`（"01"），原始记录里叫 `id`；两个都试。
                # read_problem_case 内部会 int()，"01" 与 1 等价。
                f_in, f_ans = localoj.read_problem_case(
                    prob.get("pid"), case.get("id") or case.get("no"))
                c_in = c_in or f_in
                c_ans = c_ans or f_ans
            except Exception:          # 读数据文件失败不影响其它字段
                pass
        has_content = bool(c_in or c_out or c_ans)
        self._json({
            "ok": True,
            "no": str(case.get("no") or want),
            "code": store.code_of(prob),
            "title": title,
            "status": case.get("status"),
            "status_text": case.get("status_text") or hydro.status_text(case.get("status")),
            "cls": "ok" if _tp_ok(case) else "err",
            "time_text": _fmt_ms(case.get("time")),
            "memory_text": _fmt_kb(case.get("memory")),
            "score": int(case.get("score") or 0),
            "full": full,
            "input": c_in,
            "output": c_out,
            "answer": c_ans,
            "truncated": bool(case.get("truncated")),
            "has_content": has_content,
            "message": case.get("message") or "",
            "detail_file": case.get("detail_file") or "",
            "verdict": _case_verdict(case, full),
            "foot": ("这一点的输入与标准答案来自题目数据文件；判定/用时/内存来自判分记录。"
                     "学生输出评测机不保存，所以看不到。"
                     if has_content else
                     "这一点的数据来自这笔判分记录（第 %s 题 %s 的第 %s 个测试点）。"
                     % (pno, store.code_of(prob), str(case.get("no") or want))),
        })

    def _submit_as_admin(self, pid: str, source: str, ext: str = ".cpp") -> tuple[bool, str, dict]:
        """跑一份代码的**全部测试点**，返回 (是否成功, 错误说明, 结果行)。

        「自己测试」和建题后的「标程自测」都走这里。名字是历史遗留（以前"以评测站
        管理员身份提交"）；判题换成本机沙箱之后，这里就是**本地判一遍** ——
        结果行的形状（`status` / `testcases` / `time` / `memory`）与旧的评测记录一致，
        所以下面渲染逐点明细的代码原样能用。
        """
        rec = load_problem_info().get(pid) or {}
        code = make_problem.code_of_pid(pid) or str(rec.get("name") or "") or "main"
        full = 100                      # 「自己测试」看的是点数与用时，满分按 100 折算
        try:
            row = localoj.judge(pid, source, ext, code=code, full=full, io_mode="auto")
        except Exception as e:                                # noqa: BLE001
            return False, f"判题失败（{e}）", {}
        if row.get("note"):
            # 编译不过 / 本机没这道题的数据：直接当失败回报，并把编译器报错原文带上
            # （老师最需要看的就是这个 —— 整改清单 1.7 的要求）
            msg = str(row["note"])
            cerr = row.get("compilerTexts") or []
            if cerr:
                msg += "：\n" + "\n".join(str(x) for x in cerr)[:800]
            return False, msg, row
        return True, "", row

    def _selftest_verdict(self, cases: list, time_ms: int, passed: int, total: int,
                          slowest: int, peak_kb) -> str:
        """「自己测试」的结果结论（含**最慢的点超过时限 50%** 的预警）。"""
        if not cases:
            return ('<div class="flash flash-info">评测机没有回传逐点数据（可能还在评测、'
                    '或这道题的数据有问题）。</div>')
        if passed == total:
            head = f'<b>{total} 个测试点全部通过。</b>'
        else:
            head = f'<b>{total} 个测试点，只过了 {passed} 个。</b>数据或标程有问题，别急着加进比赛。'
        tail = ""
        if time_ms:
            pct = round(slowest / time_ms * 100) if slowest else 0
            tail = (f'最慢的点 {slowest}ms（时限 {time_ms}ms，占 {pct}%），'
                    f'峰值内存 {_fmt_kb(peak_kb)}。')
            if pct > 50:
                tail += ('<br><span class="warn">注意：最慢的点已经用掉一半以上时限，'
                         '考场机器可能更慢，建议放宽时限或优化标程。</span>')
        else:
            tail = (f'最慢的点 {slowest}ms，峰值内存 {_fmt_kb(peak_kb)}'
                    '（这道题没记下时限，别忘了自己对一下题面里的限制）。')
        return f'<div class="flash flash-{"ok" if passed == total else "err"}">{head}{tail}</div>'

    def _api_selftest(self):
        """「自己测试」：把代码跑一遍这道题的全部测试点（POST，返回 JSON）。"""
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        form = self._form()
        pid = (form.get("pid") or "").strip()
        source = form.get("source") or ""
        if not pid or not source.strip():
            self._json({"ok": False, "error": "缺少题目或代码。请点题目行的「自己测试」再填代码。"}, 400)
            return
        ok, err, row = self._submit_as_admin(pid, source)
        if not ok:
            self._json({"ok": False, "error": err}, 200)
            return
        cases = row.get("testcases") or []
        rec = load_problem_info().get(pid) or {}
        time_ms = int(rec.get("time_ms") or 0)
        passed = sum(1 for c in cases if _tp_ok(c))
        slowest = max([int(c.get("time") or 0) for c in cases] or [0])
        peak = max([int(c.get("memory") or 0) for c in cases] or [0])
        self._json({
            "ok": True,
            "record_id": row.get("record_id", ""),
            "status_text": hydro.status_text(row.get("status")),
            "total": len(cases),
            "passed": passed,
            "time_ms": time_ms,
            "slowest_ms": slowest,
            "peak_kb": peak,
            "cases": [{"no": str(c.get("no") or i).zfill(2),
                       "status_text": c.get("status_text") or hydro.status_text(c.get("status")),
                       "mark": _tp_mark(c.get("status"))[0],
                       "cls": _tp_mark(c.get("status"))[1],
                       "time_text": _fmt_ms(c.get("time")),
                       "memory_text": _fmt_kb(c.get("memory"))}
                      for i, c in enumerate(cases, 1)],
            "verdict": self._selftest_verdict(cases, time_ms, passed, len(cases), slowest, peak),
        })

    def _api_problem_catalog(self, q: dict):
        """题库清单（配题用的接口）：pid / 标题 + **题目编号**（T00001）+ 默认英文名。

        接口只回评测站那边有的字段（pid/标题），编号与英文名来自考试服务自己的登记表
        （`data/problem_codes.json`）——配题的表格要显示这两列。
        """
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        refresh = q.get("refresh") in ("1", "true", "yes")
        cat = store.load_catalog()
        items = cat.get("items") or []
        err = ""
        if refresh or not items:
            try:
                items = localoj.list_problems()
                store.save_catalog(items)
                cat = store.load_catalog()
            except hydro.HydroError as e:
                err = f"读取评测站题库失败：{e}"
        codes = make_problem.load_codes()
        info = load_problem_info()
        out = []
        for it in items:
            pid = str((it or {}).get("pid") or "")
            # 假删除的题不进配题候选：它还在评测站上（题面/记录都保留），
            # 但老师已经把它从列表里收起来了，要用得先去「已删除的题目」恢复。
            if (info.get(pid) or {}).get("deleted"):
                continue
            out.append({
                "pid": pid,
                "title": str((it or {}).get("title") or ""),
                "number": make_problem.code_of_pid(pid, codes),
                "name": make_problem.name_of_pid(pid, codes),
            })
        self._json({"ok": not err, "error": err, "items": out,
                    "count": len(out), "fetched_at": cat.get("fetched_at", "")})

    def _scan_result_html(self, det: dict, fields: dict) -> str:
        """把「识别到的内容」渲染成表（自动识别完了给老师核对用）。"""
        if not det.get("count"):
            return ('<div class="flash flash-err">没认出题目结构：文件里没有成对的 '
                    '<code>.in</code>/<code>.out</code> 测试数据。请确认选的是<b>整个出题文件夹</b>'
                    '（里面有题目.md、data/、标程.cpp），或者成对的 .in/.out。</div>')
        rows = []
        for prob in det["problems"]:
            cases = len(prob.get("cases") or [])
            big = len(prob.get("big_samples") or [])
            sml = len(prob.get("samples") or [])
            code = make_problem.code_of_pid(prob["pid"]) or "（建题时分配）"
            rows.append(
                f'<tr><td><b>{html.escape(code)}</b></td>'
                f'<td>{html.escape(prob.get("title") or "")}</td>'
                f'<td>{"✓ 已读入" if prob.get("statement") else "<span class=muted>无</span>"}</td>'
                f'<td>{"✓ 已找到" if prob.get("std") else "<span class=muted>无</span>"}</td>'
                f'<td>{"✓ " + str(cases) + " 组" if cases else "<span class=err>没有</span>"}</td>'
                f'<td>{("✓ " + str(big) + " 组") if big else "<span class=muted>无</span>"}</td>'
                f'<td>{("✓ " + str(sml) + " 组，不参与评测") if sml else "<span class=muted>无</span>"}</td></tr>')
        head = (f'已识别出 <b>{det["count"]} 道题</b>，下面的内容都已自动填好 —— 直接改就行。'
                if det["count"] == 1 else
                f'识别到 <b>{det["count"]} 道题</b>，建题时会<b>全部导入</b>'
                f'（标题/编号按每题自己的信息取，本页的标题与题面只在单题时作为兜底）。')
        return (f'<div class="flash flash-ok" style="margin-top:12px">{head}</div>'
                f'<div class="card" style="background:#f8fafc">'
                f'<b>识别到的内容</b>'
                f'<table style="margin-top:8px"><tr><th>题目编号</th><th>标题</th><th>题面</th>'
                f'<th>标程</th><th>测试数据</th><th>大样例</th><th>题目样例</th></tr>'
                f'{"".join(rows)}</table>'
                f'<p class="muted" style="margin-bottom:0">题目标识由系统分配，老师不用填；'
                f'题目编号可以自己定（见下面 ②）。大样例只给学生本机调试，不参与评测。</p></div>')

    def _admin_statement(self, q: dict, flash: str = "", flash_kind: str = "ok"):
        """**改题面**（已有题目，不用删了重建）：一个 Markdown 编辑框 + 预览 + 保存。

        题面的"真身"在**评测站**那边（题目文档的 `content` 字段，`localoj.problem_statement()`
        读的就是它）；考试服务这边 `data/statements/<pid>.md` 只是**6 小时缓存**。
        所以保存要**两处都写**：先写评测站，成功后再把缓存覆盖成新文本 ——
        只写缓存的话，缓存一过期题面就"自己变回去"了（老师会以为白改了，踩过这个坑的机制见
        `hydro_client.set_problem_statement` 的注释）。

        编辑时**以评测站上的为准**（`ttl=0` 强制重取）：缓存可能过期、也可能被人手改过，
        拿缓存当编辑起点会把过期的题面写回去。评测站取不到（容器没起来）时函数会退回缓存，
        页面顶部会提示。
        """
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        pid = (q.get("pid") or "").strip()
        info = load_problem_info().get(pid) or {}
        code = make_problem.code_of_pid(pid) or str(info.get("code") or "")
        gname = make_problem.name_of_pid(pid) or str(info.get("name") or "")
        title = str(info.get("title") or "")
        cache_dir = os.path.join(store.DATA_DIR, "statements")
        text, warn = "", ""
        if not pid:
            warn = "没有指定要改哪道题。"
        else:
            try:
                text = localoj.problem_statement(pid, cache_dir=cache_dir, ttl=0)
            except Exception as e:                        # noqa: BLE001
                warn = f"从评测站取题面失败（下面是本地缓存里那份）：{e}"
                text = self._statement_raw(pid, cache_dir)
            if not text.strip():
                warn = warn or ("这道题在评测站上还没有题面 —— 保存之后就有了。")
                text = self._statement_raw(pid, cache_dir)
        head_json = _js_json(f'<code>{code or pid}</code> {title}'
                             + (f' · <code>{gname}</code>' if gname else ''))
        body = f"""
{self._admin_nav(key)}
<p><a class="btn btn-gray" href="{admin_url(key, path='/admin/problems')}">← 题目列表</a></p>
<h2>改题面</h2>
{self._flash(flash_kind, flash) if flash else ""}
{warn and f'<p class="warn">{html.escape(warn)}</p>' or ""}
<p class="muted">正在改：<b>{html.escape(title or pid)}</b>
   （题目编号 <b>{html.escape(code or "—")}</b>，英文名 <code>{html.escape(gname or "—")}</code>）<br>
   学生打开题面页看到的就是这里的内容。<b>保存后立刻生效</b>（评测站那份和本站缓存一起更新）。
   只改题面 —— 测试数据、时限、标程都不动。</p>
<form method="post" action="{statement_url(key, pid)}">
<input type="hidden" name="pid" value="{html.escape(pid, quote=True)}">
<div class="card">
  <p style="margin-top:0"><b>题面</b> <span class="muted">（Markdown，支持 $…$ 公式）</span>
     <button type="button" class="btn btn-sm btn-gray" data-modal-open="pv-modal">预览</button>
     <span class="muted">　在独立窗口里看学生打开题面时的样子（改题面会实时更新，Esc 关闭）</span></p>
  <textarea id="stmt-src" name="statement" spellcheck="false"
            style="min-height:420px">{html.escape(text)}</textarea>
  <p style="margin-top:14px"><button type="submit">保存题面</button>
     <span class="muted">（写评测站 + 刷本站缓存）</span></p>
</div>
</form>
{_view_modal()}
{_math_js()}
<script>
/* 预览：与「新建题目」页同一套做法 —— 交给服务端渲染（一份 Markdown 逻辑），
   预览里看到的和学生看到的才是同一个东西。 */
(function () {{
  var src = document.getElementById('stmt-src');
  var box = document.getElementById('pv-body');
  var modal = document.getElementById('pv-modal');
  var head = document.getElementById('pv-head');
  var timer = null;
  function draw() {{
    if (head && !head.innerHTML) head.innerHTML = {head_json};
    box.innerHTML = '<p class="muted">正在渲染…</p>';
    var body = new URLSearchParams();
    body.set('render', '1');
    body.set('statement', src.value);
    fetch('{admin_url(key, path='/admin/scan')}', {{ method: 'POST', body: body }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        if (!d.ok) {{ box.innerHTML = '<p class="err">' + (d.error || '渲染失败') + '</p>'; return; }}
        box.innerHTML = d.html || '<p class="muted">（题面还是空的）</p>';
        cspRenderMath(box);
      }})
      .catch(function (e) {{ box.innerHTML = '<p class="err">渲染失败：' + e + '</p>'; }});
  }}
  var openBtn = document.querySelector('[data-modal-open=pv-modal]');
  if (openBtn) openBtn.addEventListener('click', draw);
  if (src) src.addEventListener('input', function () {{
    if (!modal || modal.hidden) return;
    clearTimeout(timer);
    timer = setTimeout(draw, 400);
  }});
}})();
</script>"""
        self._send(page(f"改题面 · {title or pid}", body, math=True))

    def _statement_raw(self, pid: str, cache_dir: str) -> str:
        """只读本地缓存里那份题面原文（Markdown，不渲染）。"""
        path = os.path.join(cache_dir, f"{pid}.md")
        if not pid or not os.path.isfile(path):
            return ""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _admin_statement_post(self):
        """保存题面：**先写评测站**（真身），成功后再把本地缓存覆盖掉。"""
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        if make_problem is None:
            self._redirect(admin_url(key, path="/admin/problem", msg="服务端缺少 make_problem.py"))
            return
        form = self._form()
        pid = (form.get("pid") or q.get("pid") or "").strip()
        text = form.get("statement") or ""
        back = statement_url(key, pid)
        if not pid:
            self._redirect(admin_url(key, path="/admin/problems", msg="没有指定要改哪道题。"))
            return
        r = localoj.set_problem_statement(pid, text)
        if not r.get("ok"):
            log(f"[管理端] 改题面失败 {pid}：{r.get('error')}")
            self._redirect(statement_url(key, pid, msg=f"保存失败：{r.get('error')}（题面没有改动）"))
            return
        # 评测站写成功了，再把本站缓存刷成新文本（否则读的还是旧题面）
        cache_dir = os.path.join(store.DATA_DIR, "statements")
        wrote_cache = False
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, f"{pid}.md"), "w", encoding="utf-8",
                      newline="\n") as f:
                f.write((text or "").replace("\r\n", "\n").replace("\r", "\n"))
            wrote_cache = True
        except OSError as e:
            log(f"[管理端] 改题面 {pid}：评测站写了，但刷本地缓存失败 {e!r}")
        title = str((load_problem_info().get(pid) or {}).get("title") or pid)
        log(f"[管理端] 改题面 {pid}（{title}）：评测站 {r.get('field')} 已更新"
            + ("，本地缓存已刷新" if wrote_cache else "，本地缓存刷新失败"))
        msg = (f"题面已保存（{title}）—— 学生现在打开题面看到的就是新的了。"
               if wrote_cache else
               f"评测站上的题面已更新，但本站缓存没刷上（{title}）—— 最多 6 小时后会自己同步，"
               f"着急的话重启一下服务。")
        self._redirect(statement_url(key, pid, msg=msg))

    def _admin_problem(self, q: dict, flash: str = "", scan: dict | None = None,
                       flash_kind: str = "ok"):
        """新建题目：**一个表单** —— 选出题文件夹（浏览器就地识别、自动填表）→ 核对 → 建题。

        识别在浏览器里做（见页面里那段 JS），文件**只在点「建题」时才上传**。

        `flash` 是**没包过 HTML 的纯文本**（`flash_kind` 决定它是绿框还是红框）——
        调用方别再自己套 `_flash()`，否则会双重包裹、页面上显示一串转义过的标签。

        `scan` 只在**建题失败回显**时用得上（`scan` 就是服务端那次识别/报错的结果）：
        * `det`    服务端认出来的结构（题面/标程/数据/大样例）—— 让老师看到它到底认出了什么
        * `fields` 老师填过的值（标题、时限、内存、题面、标程……原样回填）
        """
        key = q.get("key", "")
        if not self._check_admin(key):
            self._admin_login()
            return
        if make_problem is None:
            self._send(page("新建题目", self._flash("err", "服务端缺少 make_problem.py。")), 500)
            return
        scan = scan or {}
        det = scan.get("det") or {}
        f = scan.get("fields") or {}
        # 已识别到的单题：题面/标程/标题都自动填进表单（多题时按每题自己的信息建，填了也不生效）
        # 正常进页面时 det 是空的（识别在浏览器里做，见页面里那段 JS）；只有**建题失败回显**时
        # 才带着服务端认出来的 det 回来，那样老师能看到服务端到底认出了什么。
        prob = det["problems"][0] if det.get("count") == 1 else None
        title_val = str(f.get("title") or (prob or {}).get("title") or "")
        if f.get("statement"):
            stmt_val = str(f["statement"])            # 老师改过：以他改的为准
        elif prob is not None:
            stmt_val = str(prob.get("statement") or "")   # 从文件夹里的 题目.md 读来的
        else:
            stmt_val = ""
        std_val = ""
        if prob is not None:
            std_val = str(prob.get("std_source") or "")
        code_val = str(f.get("name") or f.get("code") or "")   # 默认英文名（老字段名 code 也认）
        if not code_val and prob is not None:
            code_val = make_problem.name_of_pid(prob["pid"]) or ""
        time_val = str(f.get("time_ms") or 1000)
        memory_val = str(f.get("memory_mb") or 256)
        # 只有建题失败回显时才拿得到服务端的识别结果（正常进页面是浏览器里认的）
        scan_html = self._scan_result_html(det, f) if det else ""
        body = f"""
{self._flash(flash_kind, flash) if flash else ""}
{self._admin_nav(key)}
<p><a class="btn btn-gray" href="{admin_url(key)}">← 比赛列表</a>
   <a class="btn btn-gray" href="{admin_url(key, path='/admin/problems')}">题目列表</a></p>
<h2>新建题目</h2>
<form method="post" action="{admin_url(key, path='/admin/problem')}" enctype="multipart/form-data" id="mk-form">
<div class="card">
<p style="margin-top:0"><b>① 选出题文件夹</b>（选整个文件夹，或打包好的 zip）</p>
<p><input type="file" name="folder" id="mk-folder" webkitdirectory directory multiple
        style="padding:10px;background:#f0f9ff;border:1px dashed #38bdf8;border-radius:8px;width:100%"></p>
<p class="muted">也可以只传一个 zip：<input type="file" name="data" id="mk-zip" accept=".zip"></p>
<p>
  <button type="button" class="btn btn-sm btn-gray" id="mk-clear">清空已选文件</button>
  <span class="muted" id="mk-clear-note">选完文件夹之后，浏览器会<b>一直占着</b>这些文件
    （在 Windows 上就表现为「文件正在被使用，改不了 / 删不掉」）—— 要在电脑上改文件，
    先点这个「清空已选文件」，改完再重新选一次就行。<b>点「建题」之后，文件一传完就自动清空</b>，
    不用管。</span>
</p>
<p class="muted">系统按出题工程的结构自动识别，例如：<br>
<code>题目库/G01-加边后最小生成树/题目.md</code>（题面）、<code>…/标程.cpp</code>（标程）、
<code>…/data/01.in 01.out</code>（评测数据）、<code>…/大样例/大样例.in/out</code>（大样例，学生可下载）、
<code>…/data/样例1.in/out</code>（题目样例，不参与评测）。
文件夹里有多道题时会<b>全部导入</b>。<br>
<b>选完就在本机认一遍</b>：文件不上传，认出来的标题/题面/标程会直接填进下面，核对后再点「建题」。</p>
<div id="mk-detect"></div>
{scan_html}
</div>

<div class="card">
<p><b>② 标题</b> <span class="muted">（已从文件夹名读出，可改；多道题时以每题自己的为准）</span></p>
<input type="text" name="title" value="{html.escape(title_val)}" placeholder="例如 糖果分配">

<div class="row" style="margin-top:12px">
  <span><b>英文名（默认）</b></span>
  <input type="text" name="name" value="{html.escape(code_val)}"
         placeholder="可留空，例如 candy" style="width:230px">
  <span class="muted">学生用它建文件夹、命名源文件、写 <code>freopen</code>；
   只能用小写字母、数字、下划线。</span>
</div>
<p class="muted" style="margin:4px 0 0">
  这里是<b>默认值</b>：加进某场比赛时会自动填进那场比赛的「英文名」列，可以按场次再改
  （同一道题在不同场次可以叫不同的名字）。留空也行 —— 加进比赛时按本场顺序给
  <code>p1</code>、<code>p2</code>…，老师在那时改成想要的名字。
  <br><b>题目编号</b>（<code>T00001</code>）由系统分配、跟着题走，老师不用填、也不能改，
  只用它定位查找。
</p>

<div class="row" style="margin-top:12px">
  <span>时限(毫秒)</span><input type="text" name="time_ms" value="{html.escape(time_val)}" style="width:90px">
  <span>内存(MB)</span><input type="text" name="memory_mb" value="{html.escape(memory_val)}" style="width:80px">
</div>

<p style="margin-top:14px">
  <b>③ 题面</b> <span class="muted">（Markdown，支持 $…$ 公式；选出题文件夹时会自动读入 题目.md）</span>
  <button type="button" class="btn btn-sm btn-gray" data-modal-open="pv-modal" style="margin-left:6px">预览</button>
  <span class="muted">　在独立窗口里看学生打开题面时的样子（改题面会实时更新，Esc 关闭）</span>
</p>
<textarea id="stmt-src" name="statement" placeholder="# 题目描述&#10;&#10;……"
          style="min-height:220px">{html.escape(stmt_val)}</textarea>

<p style="margin-top:14px">
  <b>④ 标程</b> <span class="muted">（识别到就填进下面的框，可以直接看、直接改）</span>
</p>
<div class="codebox-bar">
  <span><span class="ok">✓</span> 标程 · <span id="std-count">—</span></span>
  <span>建完想验数据/时限，去「题目列表」点「自己测试」跑一遍</span>
</div>
<textarea class="codebox" id="std-src" name="std_text" spellcheck="false">{html.escape(std_val)}</textarea>
<p class="muted">也可以直接传文件：<input type="file" name="std"
   style="padding:6px;width:auto">（文件名用 <code>标程.cpp</code>/<code>std.cpp</code>/
   <code>solution.cpp</code> 都能认出来；上面框里填了就以框里为准）</p>

<p style="margin-top:14px"><b>⑤ 大样例</b>（单独设置，可选）</p>
<p><input type="file" name="bigsample" multiple
        style="padding:8px;background:#fffbeb;border:1px dashed #f59e0b;border-radius:8px;width:100%"></p>
<p class="muted">这里传的大样例会存到考试服务，<b>学生在考试页点「下载大样例」就能拿走</b>
（像 CSP 复赛那样可以在本机看大样例）；它<b>不参与评测、不影响分数</b>。
出题文件夹里的 <code>大样例/</code> 目录会自动收进来。</p>
<p style="margin-top:14px"><button type="submit">建题并导入评测站</button></p>
<style>
/* 上传进度条（只这一页用；样式跟着页面走，不占全局 CSS） */
.up-bar {{ height: 10px; background: #e2e8f0; border-radius: 6px; overflow: hidden; }}
.up-bar > i {{ display: block; height: 100%; width: 0; background: #2563eb; }}
.up-bar.busy > i {{
  width: 100% !important;
  background-image: linear-gradient(45deg, rgba(255,255,255,.4) 25%, transparent 25%,
                    transparent 50%, rgba(255,255,255,.4) 50%, rgba(255,255,255,.4) 75%,
                    transparent 75%);
  background-size: 18px 18px;
  animation: csp-up .9s linear infinite;
}}
@keyframes csp-up {{ from {{ background-position: 0 0; }} to {{ background-position: 18px 0; }} }}
</style>
<div id="mk-up" hidden style="margin-top:14px;padding:12px;border:1px solid #bae6fd;
     background:#f0f9ff;border-radius:8px">
  <p style="margin:0 0 8px"><b id="mk-up-title">正在上传…</b>
     <span class="muted" id="mk-up-pct"></span></p>
  <div class="up-bar" id="mk-up-bar"><i id="mk-up-fill"></i></div>
  <p class="muted" id="mk-up-note" style="margin:8px 0 0"></p>
</div>
<p class="muted">建完之后这道题立刻可以加进比赛：到比赛页「本场题目」里按<b>标题</b>搜索即可。<br>
点「建题并导入评测站」之后<b>这里会显示进度</b>：先报上传百分比，传完再提示服务端在建题
（一道题十几秒，数据多时更久）。<b>期间别关页面</b>。</p>
</div>
</form>

{_view_modal()}
{_math_js()}
<script>
/* ---- 题面预览：按学生视角渲染（服务端渲染好的 HTML，改题面会实时重渲染） ---- */
(function () {{
  var src = document.getElementById('stmt-src');
  var modal = document.getElementById('pv-modal');
  var box = document.getElementById('pv-body');
  var hint = document.getElementById('pv-hint');
  var codeEl = document.querySelector('input[name=code]');
  var titleEl = document.querySelector('input[name=title]');
  var timer = null;

  function syncHead() {{
    var code = (codeEl && codeEl.value || '').trim() || 'T1';
    var title = (titleEl && titleEl.value || '').trim() || '（未填标题）';
    var head = document.getElementById('pv-head');
    if (head) head.innerHTML = '<code>' + code + '</code> ' + title + ' · 满分 100';
    var sub = document.getElementById('pv-sub');
    if (sub) sub.textContent = '· ' + code + ' ' + title;
  }}

  /* 题面渲染走服务端（/admin/scan 的 render=1 分支只回 HTML 片段）：一份 Markdown 逻辑，
     避免"预览好好的、建完题显示不一样"。 */
  function draw() {{
    box.innerHTML = '<p class="muted">正在渲染…</p>';
    var body = new URLSearchParams();
    body.set('render', '1');
    body.set('statement', src.value);
    fetch('{admin_url(key, path='/admin/scan')}', {{ method: 'POST', body: body }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        if (!d.ok) {{ box.innerHTML = '<p class="err">' + (d.error || '渲染失败') + '</p>'; return; }}
        box.innerHTML = d.html || '<p class="muted">（题面还是空的）</p>';
        syncHead();
        cspRenderMath(box);
      }})
      .catch(function (e) {{ box.innerHTML = '<p class="err">渲染失败：' + e + '</p>'; }});
  }}

  var openBtn = document.querySelector('[data-modal-open=pv-modal]');
  if (openBtn) openBtn.addEventListener('click', function () {{
    if (hint) hint.textContent = '　预览窗口已打开，改题面会实时更新';
    draw();
  }});
  if (modal && window.MutationObserver) {{
    new MutationObserver(function () {{                 /* 窗口关掉时把提示恢复 */
      if (modal.hidden && hint) hint.textContent = '　在独立窗口里看学生打开题面时的样子（改题面会实时更新，Esc 关闭）';
    }}).observe(modal, {{ attributes: true, attributeFilter: ['hidden'] }});
  }}
  if (src) src.addEventListener('input', function () {{
    if (!modal || modal.hidden) return;
    clearTimeout(timer);
    timer = setTimeout(draw, 400);
  }});
  if (codeEl) codeEl.addEventListener('input', function () {{
    if (modal && !modal.hidden) syncHead();
  }});
  if (titleEl) titleEl.addEventListener('input', function () {{
    if (modal && !modal.hidden) syncHead();
  }});
}})();

/* ---- 标程框：实时显示行数 + Tab 插 4 个空格 ---- */
(function () {{
  var std = document.getElementById('std-src');
  var count = document.getElementById('std-count');
  if (!std || !count) return;
  function refresh() {{
    var v = std.value;
    count.textContent = (v ? v.split('\\n').length : 0) + ' 行 · ' + v.length + ' 字符' +
      (v ? '' : '（没识别到标程，可以粘贴进来）');
  }}
  std.addEventListener('input', refresh);
  std.addEventListener('keydown', function (e) {{
    if (e.key !== 'Tab') return;
    e.preventDefault();
    var s = this.selectionStart, t = this.selectionEnd;
    this.value = this.value.slice(0, s) + '    ' + this.value.slice(t);
    this.selectionStart = this.selectionEnd = s + 4;
    refresh();
  }});
  refresh();
}})();
</script>
<script>
/* ============================================================
   选完文件夹就在**本机**认一遍 —— 文件不上传，认出来的标题/题面/标程直接填进表单。
   服务端在建题时还会再认一次（**以它为准**），这里只是让老师建题前能核对一眼，
   顺便省掉"先点识别一遍、等一次上传"这一步。

   识别规则与 core/problems.detect_bundle 对齐（改了那边记得同步这里）：
   题面 题目.md/题面.md/statement.md/problem.md/README.md、标程 标程.cpp/std.cpp/…、
   数据 data|testdata|tests|test 下的「数字.in + 同名.out」、大样例 大样例/、
   题目样例 样例*.in/out、目录名 `分类号-题名`（如 G01-加边后最小生成树）。
   zip 读不了（浏览器里没有解压），那种情况提示一句让服务端去认。
   ============================================================ */
(function () {{
  var fin = document.getElementById('mk-folder');
  var zin = document.getElementById('mk-zip');
  var out = document.getElementById('mk-detect');
  if (!out) return;
  var STMT = ['题目.md', '题面.md', 'statement.md', 'problem.md', 'README.md'];
  var STD = ['标程.cpp', 'std.cpp', 'solution.cpp', 'ac.cpp', 'correct.cpp'];
  var DATA_DIRS = ['data', 'testdata', 'tests', 'test'];
  var BIG_DIRS = ['大样例', 'bigsample', 'big_samples'];
  var CODE_RE = /^([A-Z]{{1,3}}\d{{2}})-(.*)$/;

  function say(html, cls) {{
    out.innerHTML = '<p class="' + (cls || 'muted') + '" style="margin:10px 0 0">' + html + '</p>';
  }}
  function base(p) {{ var i = p.lastIndexOf('/'); return i < 0 ? p : p.slice(i + 1); }}
  function esc(s) {{
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}
  function isIn(n) {{ return /\.in$/i.test(n); }}

  /* 把 FileList 整理成「路径 → File」和「目录 → 直接子项列表」两张表 */
  function index(files) {{
    var map = {{}}, dirs = {{}};
    Array.prototype.forEach.call(files, function (f) {{
      var p = f.webkitRelativePath || f.name;
      map[p] = f;
      var parts = p.split('/');
      for (var d = 1; d < parts.length; d++) {{
        var pre = parts.slice(0, d).join('/');
        (dirs[pre] = dirs[pre] || []).push(parts.slice(d).join('/'));
      }}
    }});
    return {{ map: map, dirs: dirs }};
  }}

  /* 某个目录下的「直接子文件」和「直接子目录名」。
     注意别只看前者：index 里存的是**相对路径**（"data/01.in"），所以子目录名
     （"data"）不会自己作为一项出现，得从带斜杠的路径里切出来（踩过：数据/大样例数成 0）。 */
  function children(pre, idx) {{
    var files = [], sub = {{}};
    (idx.dirs[pre] || []).forEach(function (c) {{
      var i = c.indexOf('/');
      if (i > 0) sub[c.slice(0, i)] = true; else files.push(c);
    }});
    return {{ files: files, sub: sub }};
  }}

  /* 找出题目目录：自己直接装了题面/标程/数据的那种，且取最浅的（外层容器不算） */
  function problemDirs(idx) {{
    var found = [];
    Object.keys(idx.dirs).sort().forEach(function (pre) {{
      var ch = children(pre, idx);
      var has = ch.files.some(function (c) {{
        return STMT.indexOf(c) >= 0 || STD.indexOf(c) >= 0 || isIn(c);
      }}) || Object.keys(ch.sub).some(function (d) {{ return DATA_DIRS.indexOf(d) >= 0; }});
      if (has) found.push(pre);
    }});
    return found.filter(function (pre) {{
      return !found.some(function (o) {{ return o !== pre && pre.indexOf(o + '/') === 0; }});
    }});
  }}

  function nameOf(pre) {{
    var b = base(pre);
    var m = CODE_RE.exec(b);
    if (m) return {{ pid: m[1], title: m[2] || b }};
    return {{ pid: (b.replace(/[^0-9A-Za-z]+/g, '') || 'P').slice(0, 20), title: b }};
  }}

  /* 数一数：几组评测数据、几个大样例文件、几组题目样例 */
  function tally(pre, idx) {{
    var ch = children(pre, idx);
    var dataDir = '', bigDir = '';
    Object.keys(ch.sub).forEach(function (d) {{
      if (!dataDir && DATA_DIRS.indexOf(d) >= 0) dataDir = d;
      if (!bigDir && BIG_DIRS.indexOf(d) >= 0) bigDir = d;
    }});
    var where = dataDir ? (pre + '/' + dataDir) : pre;
    var files = idx.dirs[where] || [];
    var cases = 0, sml = 0;
    files.forEach(function (n) {{
      if (n.indexOf('/') >= 0 || !isIn(n)) return;
      var stem = n.replace(/\.in$/i, '');
      var hasOut = ['out', 'ans'].some(function (e) {{
        return Object.prototype.hasOwnProperty.call(idx.map, where + '/' + stem + '.' + e);
      }});
      if (!hasOut) return;
      if (/样例/.test(stem)) sml += 1; else cases += 1;
    }});
    var big = bigDir ? (idx.dirs[pre + '/' + bigDir] || []).filter(function (n) {{
      return n.indexOf('/') < 0;
    }}).length : 0;
    return {{ cases: cases, big: big, sml: sml }};
  }}

  function findFile(pre, names, idx) {{
    var files = children(pre, idx).files;
    for (var i = 0; i < names.length; i++) {{
      if (files.indexOf(names[i]) >= 0) return pre + '/' + names[i];
    }}
    return '';
  }}

  var idxMap = {{}};

  async function readText(path) {{
    var f = idxMap[path];
    if (!f) return '';
    try {{ return await f.text(); }} catch (e) {{ return ''; }}
  }}

  async function detect(files) {{
    var idx = index(files);
    idxMap = idx.map;
    var dirs = problemDirs(idx);
    if (!dirs.length) {{
      say('没认出题目结构。<br>要的是出题工程的样子：题目目录里有 <code>题目.md</code>、'
          + '<code>标程.cpp</code>、<code>data/</code> 下的成对 .in/.out。'
          + '或者也可以只选成对的 .in/.out 文件（那种直接点「建题」就行）。', 'warn');
      return;
    }}
    var lines = [];
    if (dirs.length > 1) {{
      var names = dirs.map(function (p) {{ return nameOf(p).pid; }});
      lines.push('<b>识别到 ' + dirs.length + ' 道题</b>（'
                 + esc(names.slice(0, 8).join('、')) + (names.length > 8 ? '…' : '')
                 + '）。<b>建题时会全部导入</b> —— 下面的标题/题面/标程对多题不生效，'
                 + '每道题的信息以各自文件夹里的为准。');
    }} else {{
      var pre = dirs[0];
      var nm = nameOf(pre);
      var t = tally(pre, idx);
      var stmtPath = findFile(pre, STMT, idx);
      var stdPath = findFile(pre, STD, idx);
      // 填表单：标题、题面、标程（选完就填好，不用再点一次"识别"）
      var tf = document.querySelector('input[name=title]');
      if (tf && !tf.value.trim()) tf.value = nm.title;
      var ss = document.getElementById('stmt-src');
      if (ss && !ss.value.trim() && stmtPath) {{
        ss.value = await readText(stmtPath);
        ss.dispatchEvent(new Event('input'));
      }}
      var sd = document.getElementById('std-src');
      if (sd && !sd.value.trim() && stdPath) {{
        sd.value = await readText(stdPath);
        sd.dispatchEvent(new Event('input'));
      }}
      lines.push('<b>识别到出题工程结构</b>：' + esc(base(pre))
                 + '（分类号 <code>' + esc(nm.pid) + '</code>，题名 '
                 + esc(nm.title) + '）：题面' + (stmtPath ? '有' : '<b>无</b>')
                 + '、标程' + (stdPath ? '有' : '<b>无</b>')
                 + '、<b>' + t.cases + '</b> 组评测数据'
                 + '、' + t.big + ' 个大样例文件'
                 + '、' + t.sml + ' 组题目样例。');
      lines.push('上面这些已经填进下面的表单了（题面/标程可以直接改）。'
                 + '点「建题」时<b>整个文件夹一起上传</b>，服务端再核一遍。');
    }}
    say(lines.join('<br>'));
  }}

  if (fin) {{
    fin.addEventListener('change', function () {{
      if (!fin.files || !fin.files.length) {{ out.innerHTML = ''; return; }}
      say('正在本机识别…');
      detect(fin.files).catch(function (e) {{ say('识别时出错：' + esc(e), 'err'); }});
    }});
  }}
  if (zin) {{
    zin.addEventListener('change', function () {{
      if (!zin.files || !zin.files.length) return;
      say('zip 不在浏览器里预览（读不了压缩包）—— 直接点「建题并导入评测站」，'
          + '服务端会解开并把里面认出来。');
    }});
  }}
}})();
</script>
<script>
/* ============================================================
   建题上传：给个大概的进度。

   原来的做法就是普通表单 POST —— 点完之后页面一动不动，几十 MB 的出题文件夹
   要传一会儿，老师根本不知道是在传、还是卡住了。现在改成 XHR 上传，分两段显示：

     ① 传输阶段：用 `xhr.upload.onprogress` 报**真实百分比**（还有已传/总大小、速度）
     ② 服务端阶段：传完（`xhr.upload.onload`）之后进度条变成流动条纹，提示
        "正在解包/配对测试点/导入评测站，别关页面" —— 这一段服务端没有细粒度进度
        （建题是同步跑的，见 core/problems.create_problem），所以只报"在跑"，不报百分比，
        不瞎编数字。

   上传成功后跟着服务端的 302 走（`xhr.responseURL` 就是跳转后的地址），
   于是"成功/失败"的提示仍由服务端那套 `?msg=` 统一渲染，前端不另写一套文案。
   浏览器不支持 XHR/FormData 时不动表单，照旧直接提交。
   ============================================================ */
(function () {{
  var form = document.getElementById('mk-form');
  if (!form || !window.FormData || !window.XMLHttpRequest) return;
  var box = document.getElementById('mk-up');
  var bar = document.getElementById('mk-up-bar');
  var fill = document.getElementById('mk-up-fill');
  var titleEl = document.getElementById('mk-up-title');
  var pctEl = document.getElementById('mk-up-pct');
  var noteEl = document.getElementById('mk-up-note');
  var btn = form.querySelector('button[type=submit]');
  var sent = 0, lastT = 0;

  function mb(n) {{ return (n / 1048576).toFixed(1) + ' MB'; }}
  function pick() {{
    var names = ['folder', 'data', 'std', 'bigsample'], n = 0, i, el, fs;
    for (i = 0; i < names.length; i++) {{
      el = form.querySelector('input[name=' + names[i] + ']');
      fs = el && el.files;
      for (var j = 0; fs && j < fs.length; j++) n += (fs[j].size || 0);
    }}
    return n;
  }}
  /* 松手：把选中的文件从浏览器手里放掉。
     浏览器会**一直占着**你选中的文件（Windows 上就是「文件正在被使用，改不了、删不掉」），
     直到这份"选择"被丢掉、或者页面被关掉。所以：
       * 点「建题」之后，**文件一传完就自动松开**（xhr.upload.onload）—— 服务端建题那十几秒
         你已经在电脑上改文件了，不用等；
       * 想在传之前就回去改，点「清空已选文件」。
     松手只是丢掉"选择"，不影响已经发出去的请求（FormData 早就把文件抓下来了）。
     踩过：老师上传完去改自己的标程，编辑器提示"文件被占用"，一直改不了。 */
  function releasePicks() {{
    var names = ['folder', 'data', 'std', 'bigsample'], i, el;
    for (i = 0; i < names.length; i++) {{
      el = form.querySelector('input[name=' + names[i] + ']');
      if (el) {{ try {{ el.value = ''; }} catch (e) {{}} }}
    }}
    var out = document.getElementById('mk-detect');
    if (out) out.innerHTML = '';
    var note = document.getElementById('mk-clear-note');
    if (note) {{
      note.innerHTML = '已松手：浏览器不再占着这些文件，现在就能在电脑上改了。'
        + '<b>改完记得重新选一次出题文件夹</b>（文件不暂存，重选才能再建）。';
    }}
  }}
  var clearBtn = document.getElementById('mk-clear');
  if (clearBtn) clearBtn.addEventListener('click', function () {{ releasePicks(); }});

  function stop(why) {{
    form.dataset.busy = '';
    if (btn) {{ btn.disabled = false; btn.textContent = '建题并导入评测站'; }}
    if (bar) bar.classList.remove('busy');
    if (fill) fill.style.width = '0%';
    if (titleEl) titleEl.textContent = '没传成功';
    if (noteEl) {{
      /* 网络断了这种"没发出去"的情况**不松手**：文件还在选择里，重试一下就行。
         这会儿真想改文件，上面那个「清空已选文件」随时能点。 */
      noteEl.innerHTML = esc(why) + ' 再点一次「建题并导入评测站」重试；'
        + '选好的文件还在，不用重新选（这会儿想改文件，点上面的「清空已选文件」）。';
    }}
  }}
  function esc(s) {{
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }}

  form.addEventListener('submit', function (ev) {{
    if (form.dataset.busy === '1') {{ ev.preventDefault(); return; }}   /* 别点两下 */
    var bytes = pick();
    if (!bytes) return;         /* 一个文件都没选：交给服务端回提示，前端不拦 */
    ev.preventDefault();
    form.dataset.busy = '1';
    if (btn) {{ btn.disabled = true; btn.textContent = '正在建题…'; }}
    if (box) box.hidden = false;
    if (bar) bar.classList.remove('busy');
    if (fill) fill.style.width = '0%';
    if (pctEl) pctEl.textContent = '';
    if (titleEl) titleEl.textContent = '正在上传出题文件夹…';
    if (noteEl) {{
      noteEl.innerHTML = '一共约 ' + mb(bytes) + '。传完由服务端解包、配对测试点、'
        + '导入评测站。';
    }}

    var xhr = new XMLHttpRequest();
    xhr.open('POST', form.getAttribute('action'), true);
    sent = lastT = Date.now();
    xhr.upload.onprogress = function (e) {{
      var now = Date.now(), kbs;
      if (!e.lengthComputable) {{
        if (titleEl) titleEl.textContent = '正在上传…';
        if (noteEl) noteEl.innerHTML = '已传 ' + mb(e.loaded) + '（浏览器没报总大小）';
        return;
      }}
      var p = e.loaded / e.total * 100;
      if (fill) fill.style.width = p.toFixed(1) + '%';
      if (pctEl) pctEl.textContent = p.toFixed(0) + '%';
      if (now - lastT < 400) return;              /* 每 0.4 秒刷一次就够了 */
      kbs = (e.loaded - sent) / 1024 / Math.max(0.001, (now - lastT) / 1000);
      sent = e.loaded;
      lastT = now;
      if (noteEl) {{
        noteEl.innerHTML = '已传 <b>' + mb(e.loaded) + '</b> / ' + mb(e.total)
          + '（约 ' + (kbs >= 1024 ? (kbs / 1024).toFixed(1) + ' MB/s'
                                   : kbs.toFixed(0) + ' KB/s') + '）';
      }}
    }};
    xhr.upload.onload = function () {{            /* 传输结束，剩下是服务端干活 */
      releasePicks();                             /* 文件传完了就松手，老师可以马上改文件 */
      if (titleEl) titleEl.textContent = '上传完成，服务端正在建题…';
      if (pctEl) pctEl.textContent = '';
      if (fill) fill.style.width = '100%';
      if (bar) bar.classList.add('busy');
      if (noteEl) {{
        noteEl.innerHTML = '正在解包、配对测试点、导入评测站 —— 一道题通常十几秒，'
          + '数据多或题库大时更久。<b>这一段没有细粒度进度，请别关页面、别刷新</b>，'
          + '跑完会自动跳转。<br>出题文件夹已经不在浏览器手里了（可以放心去电脑上改文件）。';
      }}
    }};
    xhr.onload = function () {{
      if (xhr.status >= 200 && xhr.status < 400) {{
        var target = xhr.responseURL || '';
        /* 比的是**绝对地址** `form.action`（IDL 属性，浏览器解析过），
           不是 `getAttribute('action')`（那是相对地址字符串）—— 拿相对地址比，
           两者永远不相等，于是失败页也被当成"跳转过了"，红框提示照样丢（踩过）。 */
        if (target && target !== form.action) {{
          location.href = target;      /* 302 跳转后的地址（带 ?m=… 的成功/失败提示） */
          return;
        }}
        /* 没有跳转 = 服务端**直接把失败页回显了**（HTTP 200 + 红框提示 + 回填的字段）。
           这时候不能拿 responseURL 再 GET 一次 —— 那样提示就没了（踩过：
           提交一次假数据，页面干干净净像是成功了一样）。把服务端那份 HTML 装进来，
           于是"失败提示 + 认出来的结构 + 填过的字段"都在。 */
        document.open();
        document.write(xhr.responseText);
        document.close();
        return;
      }}
      stop('服务端返回了 ' + xhr.status + '。');
    }};
    xhr.onerror = function () {{ stop('上传时网络断了。'); }};
    xhr.ontimeout = function () {{ stop('上传超时了。'); }};
    xhr.send(new FormData(form));
  }});
}})();
</script>"""
        self._send(page("新建题目", body, math=True))

    def _problem_in_use(self, pid: str) -> list[str]:
        """这道题被哪些比赛用着（「CSP 模拟赛 第 1 题」这种）。空表 = 没在用。"""
        used = []
        for c in store.list_contests():
            for p in store.load_exam(c["id"]).get("problems", []):
                if str(p.get("pid") or "") == pid:
                    used.append(f'{c["title"]} 第 {p.get("no")} 题')
        return used

    def _set_problem_deleted(self, pid: str, flag: bool) -> None:
        """打上／取消「已删除」这个记号（就改 `problem_info.json` 里的一个字段）。

        实现在 `core/problems.set_deleted` —— 建题/题单导入那两条路也要看这个记号
        （见 `is_deleted`），所以不能只放在页面层。
        """
        make_problem.set_deleted(pid, flag)

    def _delete_problem(self, pid: str, key: str) -> None:
        """删除题目 = **假删除**：从「题目列表」里收起来，别的什么都不动。

        为什么不做真删：真删要连评测站上的题目一起删掉，于是**题面打不开、历史提交记录里
        这道题也变成空壳**。老师要的是"别再出现在列表里"，不是"抹掉证据"。所以这里只打一个
        `deleted` 记号 —— 评测站题目、题面缓存、大样例、编号登记**全部保留**，
        想彻底清掉去「已删除的题目」点「彻底删除」。

        顺带把慢的问题也解决了：真删要 `docker exec` 进容器跑 mongosh + `hydrooj cli`
        （好几秒），完了再刷一次题库缓存又是好几秒；假删除**一个 exec 都不用**，点完立刻回来。

        **删掉之后系统不再认这道题**：题库待选、配题候选、题目列表里都不再出现它，
        它也不再挡建题 —— 同一个标识再建一次会**建出一份新的**（内部标识自动让开，
        见 `core/problems.free_pid`），旧的那份原样留着。

        **还在比赛里用着的题不许删**：那一场的 `exam.json` 指着它
        （和「有人提交后不许重排考号」是同一类保护）。要删得先把那些比赛里的这道题移除。
        """
        pid = (pid or "").strip()
        if not pid:
            self._finish_problem_action(key, "没有指定要删哪道题。", ok=False)
            return
        used = self._problem_in_use(pid)
        if used:
            self._finish_problem_action(key, (
                f"「{pid}」还在比赛里用着，不能删：{'、'.join(used)}。"
                f"先从那些比赛里把它移除，再回来删。"), ok=False)
            return
        title = str((load_problem_info().get(pid) or {}).get("title") or pid)
        self._set_problem_deleted(pid, True)
        log(f"[管理端] 假删除题目 {pid}（{title}）：题面/大样例/编号/评测站都保留")
        self._finish_problem_action(key, (
            f"已删除题目 {pid}（{title}）—— 只是从列表里收起来了："
            f"题面和历史提交记录都还看得到。要找回来，在下面「已删除的题目」里点「恢复」。"
            f"这个标识不再挡建题：同一份文件夹再建一次会建出一份新的，两份互不影响。"))

    def _restore_problem(self, pid: str, key: str) -> None:
        """把假删除的题放回列表。题面/大样例/编号一直都在，所以恢复是瞬间的。"""
        pid = (pid or "").strip()
        if not pid:
            self._finish_problem_action(key, "没有指定要恢复哪道题。", ok=False)
            return
        self._set_problem_deleted(pid, False)
        title = str((load_problem_info().get(pid) or {}).get("title") or pid)
        log(f"[管理端] 恢复题目 {pid}（{title}）")
        self._finish_problem_action(key, f"已恢复题目 {pid}（{title}），它回到「全部题目」里了。")

    def _purge_problem(self, pid: str, key: str) -> None:
        """**彻底删除**：评测站题目 + 本地四处残留一起清 —— 这一步不可逆。

        清这些地方（漏一个就会在「题目列表」里留一行指向已删题目的空壳）：
          * 评测站上的题目 —— `importer.hydro_delete_problem(pid)`
          * `data/problem_codes.json` —— 题目编号登记
          * `data/problem_info.json`  —— 测试点/时限/内存这些建题时记的元信息
          * `data/statements/<pid>.md` —— 题面缓存
          * `data/samples/<pid>/`      —— 大样例存档

        慢是正常的：要进容器跑 mongosh 查 docId、再跑 hydrooj cli 删题，最后刷题库缓存。
        日常"删掉不想要的题"用假删除（`_delete_problem`）就够了，别走这条。
        """
        pid = (pid or "").strip()
        if not pid:
            self._finish_problem_action(key, "没有指定要删哪道题。", ok=False)
            return
        used = self._problem_in_use(pid)
        if used:
            self._finish_problem_action(key, (
                f"「{pid}」还在比赛里用着，不能删：{'、'.join(used)}。"
                f"先从那些比赛里把它移除，再回来删。"), ok=False)
            return
        title = str((load_problem_info().get(pid) or {}).get("title") or pid)
        done, failed = [], []
        # **本地题目数据**（测试点 + 题面）：这一版起这两样才是正本，删掉才算真删
        try:
            gone = judgelocal.drop_problem_data(pid)
            done.extend(f"本地{g}" for g in gone)
        except Exception as e:                                     # noqa: BLE001
            log(f"[管理端] 彻底删题 {pid}：删本地题目数据失败 {e!r}")
            failed.append("本地题目数据")
        codes = make_problem.load_codes()
        if pid in codes:
            codes.pop(pid)
            make_problem.save_codes(codes)
            done.append("题目编号")
        info = load_problem_info()
        if pid in info:
            info.pop(pid)
            save_problem_info(info)
            done.append("题目信息")
        # 题面缓存 + 大样例存档（和建题时作废旧缓存的实现同一份，别各写一遍）
        cache_gone = make_problem.drop_problem_cache(pid)
        if cache_gone:
            done.extend(cache_gone)
        # 题库缓存也刷新一下，别再列出这道题
        try:
            store.save_catalog(localoj.list_problems())
        except (hydro.HydroError, OSError) as e:
            log(f"[管理端] 彻底删题 {pid}：刷新题库缓存失败 {e!r}")
        log(f"[管理端] 彻底删除题目 {pid}（{title}），清掉了：{'、'.join(done) or '无（本来就没有）'}"
            + (f"；**没删掉**：{'、'.join(failed)}" if failed else ""))
        if failed:
            # 本地删不干净（权限/占用之类）：如实说，别报"已彻底删除"
            self._finish_problem_action(key, (
                f"「{pid}」（{title}）**没能删干净**：{'、'.join(failed)}。"
                f"多半是文件被占用或权限不对，可以过一会儿再点一次「彻底删除」。"
                f"（登记里的信息已经清掉了，所以它不会再出现在列表里。）"), ok=False)
            return
        self._finish_problem_action(key, (
            f"已彻底删除题目 {pid}（{title}）" +
            (f"：{'、'.join(done)}。" if done else "：本地没有它的残留。") +
            "题面与历史提交记录里这道题都会显示为已删除。"))

    def _admin_problem_post(self):
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        if make_problem is None:
            self._redirect(admin_url(key, path="/admin/problem", msg="服务端缺少 make_problem.py"))
            return
        # 非 multipart 走这条：删除题目（`action=delete&pid=…`）。
        # 必须在 `_parse_multipart` **之前**判断，也不能先调 `_form()` ——
        # `_form()` 会把 body 整个读掉，multipart 上传就没得读了。
        if "multipart/form-data" not in self.headers.get("Content-Type", ""):
            form = self._form()
            act = form.get("action") or ""
            if act in ("delete", "restore", "purge"):
                # delete = 假删除（题面/提交记录都留着）；restore = 放回列表；
                # purge = 彻底删除（真删本机题目数据，慢）
                # `json=1`（题目列表页的按钮走这条）：**只回 JSON + 两张表的 HTML**，
                # 前端就地换表格 —— 页面不跳、滚动位置不丢。
                self._want_json = q.get("json") == "1"
                {"delete": self._delete_problem,
                 "restore": self._restore_problem,
                 "purge": self._purge_problem}[act](form.get("pid", ""), key)
                self._want_json = False
                return
            self._redirect(admin_url(key, path="/admin/problem",
                                     msg="这个地址用来上传出题文件夹（multipart 表单），"
                                         "删除题目请从「题目列表」点删除。"))
            return
        try:
            fields, files, file_fields = self._parse_multipart(MAX_PS_UPLOAD)
        except ValueError as e:
            self._redirect(admin_url(key, path="/admin/problem", msg=f"上传失败：{e}"))
            return
        pid = (fields.get("pid") or "").strip()      # 题库标识（内部用；老师看不到）
        title = (fields.get("title") or "").strip()
        # 默认英文名（学生侧，可留空）。老页面这个字段叫 code（值也是英文名），一起认。
        name = (fields.get("name") or fields.get("code") or "").strip()

        def _int(name, default):
            try:
                return int(fields.get(name) or default)
            except ValueError:
                return default

        # 文件按「表单字段名」分类：
        #   folder    —— 整个出题文件夹（webkitdirectory，路径里带目录层级）
        #   data      —— 手选的成对 .in/.out（或 zip）
        #   std       —— 标程
        #   bigsample —— 大样例（单独设置，学生可下载）
        # 认字段名而不是文件名：中文文件名（标程.cpp/大样例.in）经 multipart 传输
        # 可能被编码搞乱（踩过：标程被当成测试数据丢掉了）。
        uploads, big_files = {}, {}
        std_source = ""
        for fn, field in list(file_fields.items()):
            if field == "std":
                std_source = files[fn].decode("utf-8", "replace")
            elif field == "bigsample":
                big_files[fn] = files[fn]
            elif field in ("folder", "data"):
                uploads[fn] = files[fn]
        # 标程：优先用框里填的（识别到就填进去了，老师可以直接改）
        std_source = std_source or (fields.get("std_text") or "").strip()
        statement = (fields.get("statement") or "").strip()

        # 大样例可以在建题时一起传（`bigsample` 字段），追加到识别结果里
        extra_big = make_problem.multipart_samples(big_files) if big_files else []

        if not uploads:
            self._redirect(admin_url(key, path="/admin/problem",
                                     msg="没有收到测试数据：选整个出题文件夹，或选成对的 .in/.out，"
                                         "也可以传一个 zip。"))
            return

        # 先看是不是出题工程结构、里面有几道题
        det = make_problem.detect_bundle(make_problem.unpack(uploads)[0])

        def _retry(message: str) -> None:
            """建题没成：把**服务端认出来的结果**和老师填过的字段带回建题页，改一处就能重试。

            文件不再暂存（识别改在浏览器里做了、只有点「建题」才上传），所以回显里没有
            token —— 重试要**重新选一次文件夹**。文案里说清楚这点，别让老师以为是页面丢了。
            """
            keep = {k: (fields.get(k) or "") for k in ("title", "statement", "name", "code",
                                                       "time_ms", "memory_mb")}
            if title and not keep["title"]:
                keep["title"] = title
            if name and not keep["name"]:
                keep["name"] = name
            self._admin_problem(
                q,
                # 注意别把 _flash() 的结果再塞进 flash：`_admin_problem` 自己会用
                # `flash_kind` 包一层（踩过：双重包裹 → 老师看到绿框里一串
                # 转义过的 `<div class="flash flash-err">…`）
                flash=message + "（要重试请重新选一次出题文件夹 —— 文件不做暂存了）",
                flash_kind="err",
                scan={"det": det, "fields": keep})

        if det["count"] > 1:
            # 一次导入多道题（出题工程里有很多题时）
            bundle = make_problem.create_bundle(
                uploads, time_ms=_int("time_ms", 1000), memory_mb=_int("memory_mb", 256),
                statement=statement, overwrite=False)
            if not bundle["items"]:
                _retry(bundle.get("error", "没识别出题目"))
                return
            good = [i for i in bundle["items"] if i.get("ok")]
            bad = [i for i in bundle["items"] if not i.get("ok")]
            msg = (f"识别到 {len(bundle['items'])} 道题，成功导入 {len(good)} 道"
                   + ("：" + "、".join(f"{i['title'] or i['pid']}（{i.get('number') or '没编号'}"
                                      f"· 英文名 {i.get('name') or '待填'}）"
                                      for i in good) if good else ""))
            if bad:
                msg += "；失败：" + "、".join(f"{i.get('title') or i['pid']}"
                                             f"（{(i.get('error') or '')[:40]}）" for i in bad)
            renamed = [i for i in good if i.get("pid_from")]
            if renamed:
                # 重名不再拒绝：站点上已有同名标识的那几道，这份换了内部标识，两份都在
                msg += ("；其中 " + "、".join(f"{i['pid_from']}→{i['pid']}" for i in renamed)
                        + " 站点上已有同名标识，改用了新的内部标识（老师界面只看得到题目编号）")
            for it in good:      # 记下测试点数/时限/内存/标程（「题目列表」页要用）
                remember_problem(it["pid"], title=it.get("title", ""),
                                 code=it.get("number", ""), name=it.get("name", ""),
                                 cases=it.get("cases"), time_ms=_int("time_ms", 1000),
                                 memory_mb=_int("memory_mb", 256))
            samples = [i for i in good if i.get("samples")]
            if samples:
                msg += f"；其中 {len(samples)} 道带大样例（学生可下载）"
            self._redirect(admin_url(key, path="/admin/problem", msg=msg))
            return

        # 单题：可能是识别到的出题工程，也可能是手选的扁平数据
        if not std_source:
            std_source, std_name = make_problem.find_std(uploads)
            if std_name and det["count"] == 0:
                uploads.pop(std_name, None)       # 别把标程当成测试数据
        if not statement and det["count"] == 0:
            statement, st_name = make_problem.take_statement(uploads)
            if st_name:
                uploads.pop(st_name, None)
        if det["count"] == 0:      # 扁平上传：把代码/文档类文件剔除，剩下才是数据
            for junk in list(uploads):
                base = os.path.basename(junk).lower()
                if base.endswith((".cpp", ".c", ".md")) or base.endswith(".exe"):
                    uploads.pop(junk, None)
            if not uploads:
                _retry("没有收到测试数据：选整个出题文件夹，或选成对的 .in/.out。")
                return

        rep = make_problem.create_problem(
            pid, title, uploads, time_ms=_int("time_ms", 1000),
            memory_mb=_int("memory_mb", 256), statement=statement,
            std_source=std_source, overwrite=False, name=name)
        if not rep.get("ok"):
            extra = ""
            if rep.get("problems"):
                extra = "；" + "，".join(str(x) for x in rep["problems"][:3])
            _retry(f"建题失败：{rep.get('error', '未知错误')}{extra}")
            return

        # 单独传的大样例（⑤）追加/覆盖到这道题
        if extra_big:
            info = make_problem.save_samples(rep["pid"], [extra_big, []], big_files)
            rep["samples"] = info

        # 记下这道题的元信息（测试点数/时限/内存/标程）——「题目列表」页与「自己测试」要用
        remember_problem(rep["pid"], title=rep.get("title", ""), code=rep.get("number", ""),
                         name=rep.get("name", ""),
                         cases=rep.get("cases"), time_ms=_int("time_ms", 1000),
                         memory_mb=_int("memory_mb", 256), std=std_source)

        msg = (f"题目 {rep.get('number') or rep['pid']}（{rep['title']}）已导入，"
               f"共 {rep['cases']} 组测试数据"
               f"（{', '.join(rep['case_names'][:6])}{'…' if rep['cases'] > 6 else ''}）")
        if rep.get("pid_from"):
            # 重名不再拒绝建题：站点上那份（可能是已删除的）原样留着，这份换了内部标识
            msg = (f"站点上已经有 {rep['pid_from']} 了，这份新题改用了内部标识 "
                   f"{rep['pid']}（老师界面只看得到下面的题目编号）—— 两份都在，互不影响。"
                   + msg)
        if rep.get("number"):
            msg += f"；题目编号 {rep['number']}（老师用它定位这道题）"
        if rep.get("name"):
            msg += f"；默认英文名 {rep['name']}（学生用它建文件夹、写 freopen；" \
                   f"加进比赛时可以再改）"
        else:
            msg += "；英文名没填 —— 加进比赛时再填（留空会自动给 p1/p2…）"
        if rep.get("detected"):
            msg += "；已自动识别出题工程结构（题面/标程/数据）"
        if rep.get("samples"):
            kinds = []
            for it in rep["samples"]["items"]:
                kinds.append("大样例" if it.get("kind") == "big" else "样例")
            msg += f"；大样例/样例已存档（{'、'.join(sorted(set(kinds)))}），学生可在考试页下载"
        if rep.get("sample_notes"):
            msg += "；" + "，".join(rep["sample_notes"][:2])
        if rep.get("no_answer"):
            msg += f"；注意 {len(rep['no_answer'])} 组没有答案（.out），这些点会对任何输出判错"
        self._redirect(admin_url(key, path="/admin/problem", msg=msg))

    def _admin_scan(self):
        """`/admin/scan` 现在**只干一件事**：把一段 Markdown 渲染成 HTML 回给页面。

        识别出题文件夹改到**浏览器里**做了（选完文件夹就地认、不上传，见页面里那段 JS），
        所以这里不再承接 multipart 上传。仍有两个调用点：
          * `statement=<正文>` —— 新建题目页的「预览」（学生视角）
          * `pid=<题库标识>`    —— 题目列表的「查看题面」（题面从缓存/评测站取）
        """
        q = self._query()
        key = q.get("key", "")
        if not self._check_admin(key):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        if make_problem is None:
            self._redirect(admin_url(key, path="/admin/problem", msg="服务端缺少 make_problem.py"))
            return
        if "multipart/form-data" in self.headers.get("Content-Type", ""):
            # 老页面（或历史书签/脚本）还在往这里传文件夹 —— 给一句明确的去处
            self._redirect(admin_url(key, path="/admin/problem",
                                     msg="识别出题文件夹已经改在浏览器里做了：在「新建题目」页"
                                         "选完文件夹就会自动认，不用再点「先识别一遍」。"))
            return
        fields = self._form()
        if fields.get("render") != "1":
            self._json({"ok": False, "error": "这个接口只用于题面渲染（render=1）。"}, 400)
            return
        text = fields.get("statement") or ""
        pid = (fields.get("pid") or "").strip()
        if not text.strip() and pid:
            cache_dir = os.path.join(store.DATA_DIR, "statements")
            self._json({"ok": True, "html": self._statement_html(pid, cache_dir, fetch=True)})
            return
        self._json({"ok": True, "html": md_to_html(text) if text.strip() else ""})

    def _serve_doc(self, path_: str, title: str) -> None:
        """把一份 Markdown 文档当网页渲染出来（目前只有 `/docs/problemset`）。

        **用 `md_to_html()` 渲染**，不要整个塞进 `<pre>` 里倒原文：那样页面等于把
        `#`、`**`、表格竖线原样摊给老师看（踩过，整页都是黑底等宽的原文）。
        渲染逻辑与题面页同一份（`<div class="card stmt">` 那层壳也一样），
        所以 `.stmt` 那套 CSS 直接生效：标题、表格、代码块、列表、引用都有样式。
        """
        if not os.path.isfile(path_):
            self._send(page(title, '<p class="muted">文档文件缺失。</p>'), 404)
            return
        text = open(path_, encoding="utf-8").read()
        body = ('<p><a class="btn btn-gray" href="/admin">返回管理端</a></p>'
                f'<div class="card stmt" style="overflow:auto">{md_to_html(text)}</div>')
        self._send(page(title, body))

    def _api_ps_import(self) -> None:
        q = self._query()
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        if import_problemset is None:
            self._json({"ok": False, "error": "服务端缺少 import_problemset.py"}, 500)
            return
        try:
            fields, files, _ff = self._parse_multipart(MAX_PS_UPLOAD)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        blob = files.get("file") or files.get("zipfile")
        if blob is None and files:
            blob = next(iter(files.values()))
        if not blob:
            self._json({"ok": False, "error": "没有收到题单文件（表单字段名请用 file）"}, 400)
            return
        if blob[:2] != b"PK":
            self._json({"ok": False, "error": "上传的不是 zip 文件"}, 400)
            return
        job = "ps-%d-%s" % (int(time.time()), os.urandom(3).hex())
        work = os.path.join(HERE, "ps-jobs", job)
        os.makedirs(work, exist_ok=True)
        zip_path = os.path.join(work, "problemset.zip")
        with open(zip_path, "wb") as f:
            f.write(blob)
        with PS_JOBS_LOCK:
            PS_JOBS[job] = {"state": "running", "progress": "已收到题单，开始解析…",
                            "report": None, "error": "", "started": time.time()}

        def _int(name, default):
            try:
                return int(fields.get(name) or default)
            except ValueError:
                return default

        opts = {
            "tags": [x for x in [fields.get("tag", "").strip()] if x],
            "time_ms": _int("time", 1000),
            "memory_mb": _int("memory", 256),
            "only": [x.strip() for x in (fields.get("only") or "").split(",") if x.strip()],
            # 默认英文名：与命令行 --code 同一套写法（`G01=candy` 逐题 / `candy` 整体），
            # 一个字段里可以逗号分隔多题
            "codes": import_problemset.parse_codes(
                [x.strip() for x in (fields.get("code") or "").split(",") if x.strip()]),
            "overwrite": fields.get("overwrite") in ("1", "true", "on"),
            "dry_run": fields.get("dry_run") in ("1", "true", "on"),
        }
        threading.Thread(target=_ps_worker, args=(job, zip_path, opts), daemon=True).start()
        log(f"[题单导入] 收到 {len(blob) / 1048576:.1f}MB，任务 {job}")
        self._json({"ok": True, "job": job,
                    "message": f"已接收题单（{len(blob) / 1048576:.1f} MB），正在后台导入",
                    "report_url": f"/api/problemset/report?job={job}"})

    def _api_ps_report(self, q: dict) -> None:
        if not self._check_admin(q.get("key", "")):
            self._json({"ok": False, "error": "管理密钥不正确"}, 403)
            return
        job = q.get("job", "")
        with PS_JOBS_LOCK:
            info = PS_JOBS.get(job)
            info = dict(info) if info else None
        if not info:
            self._json({"ok": False, "error": "任务号不存在"}, 404)
            return
        out = {"ok": True, "state": info["state"], "progress": info["progress"]}
        if info.get("report"):
            out["report"] = info["report"]
        if info.get("error"):
            out["error"] = info["error"]
        self._json(out)


# ---------- 题单导入的后台任务 ----------

def _ps_worker(job: str, zip_path: str, opts: dict) -> None:
    def progress(msg) -> None:
        with PS_JOBS_LOCK:
            if job in PS_JOBS:
                PS_JOBS[job]["progress"] = str(msg)[:400]

    try:
        single_code, codes = opts.get("codes") or ("", {})
        report = import_problemset.import_problemset(
            zip_path, tags=opts["tags"], time_ms=opts["time_ms"], memory_mb=opts["memory_mb"],
            only=opts["only"], overwrite=opts["overwrite"], dry_run=opts["dry_run"],
            code=single_code, codes=codes, log=progress)
        summary = (f"完成：成功 {len(report['imported'])}、跳过 {len(report['skipped'])}、"
                   f"失败 {len(report['failed'])}，耗时 {report['elapsed_s']}s")
        # 导入成功的题也记一份元信息（测试点数/时限/内存），「题目列表」页要显示这三列
        if not report.get("dry_run"):
            for item in report.get("problems") or []:
                if item.get("status") == "imported":
                    remember_problem(item.get("pid", ""), title=item.get("title", ""),
                                     code=item.get("number") or item.get("code", ""),
                                     name=item.get("name", ""),
                                     cases=item.get("case_count"),
                                     time_ms=opts.get("time_ms"),
                                     memory_mb=opts.get("memory_mb"))
        with PS_JOBS_LOCK:
            PS_JOBS[job].update(state="done" if report.get("ok") else "failed",
                                report=report, progress=summary)
        log(f"[题单导入] {job} {summary}")
    except Exception as exc:                      # noqa: BLE001
        log(f"[题单导入] {job} 失败：\n" + traceback.format_exc())
        with PS_JOBS_LOCK:
            PS_JOBS[job].update(state="failed", error=str(exc),
                                progress="导入失败：" + str(exc)[:200])
