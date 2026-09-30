"""学生端页面（考试入口、比赛、提交、交卷回执、考生须知、成绩、大样例、题面）

页面只做「取数据、套模板」：赛制、级别、时长、题目英文名这些规则都在 core/
（`store.rule_of` / `duration_of` / `level_name` / `code_of` / `SUBMIT_UPLOAD_NOTE` …），
这里不另立规则。三种赛制的提交方式**已经统一成"交考号文件夹"**，
所以提交区只有一套，不再按赛制分叉。

**零反馈是唯一行为**：判分照常跑（老师要看成绩），但老师「公布成绩」
（`exam["released"]`）之前，学生端看不到任何判定、分数、逐点状态 —— 连"部分正确"这种
状态都不给，也不提"正在判分"；公布之后才显示分数。三种赛制一视同仁。
（以前还有赛制自带的 `feedback` 与本场 `feedback_mode` 两层开关，已按需求整段删掉。）

本轮（T-08）改动：
  * 比赛页：倒计时 + 级别徽章；题目表列「题目英文名」（学生用它建文件夹、
    命名源文件、写 freopen；`T00001` 那种**题目编号**只在老师界面出现）；
    删掉题目提交状态卡片、
    交卷前检查清单、我的成绩；提交区统一上传文件夹（CSP 附广东目录结构与命名红线，
    OI/IOI 说明用标准输入输出）
  * 题面页：不再出现「本题的读写文件与提交方式」（要求都写在考生须知里）
  * 交卷回执：改成**文件结构检查清单**（文件名、目录位置、个人信息文件、大小写）
  * 新增考生须知 `GET /help?c=<比赛id>`；删掉「我的提交」页（`_result_page` 与 `/result`）
"""

from __future__ import annotations

import os
import threading
import time
import urllib.parse
import html

from ..core import grading, hydro_client as hydro, store, wrapper
from ..core.security import make_cookie
from ..core import problems as make_problem
from ..core.util import log, _fmt_bytes
from ..config import MAX_UPLOAD, NOTICE_DOC
from .multipart import _read_zip
from .urls import cid_query
from .ui import code_pre, decode_text, looks_binary
from .ui import (page, rule_badge, rule_tip, sample_link, has_samples,
                 student_status_cell, md_to_html,
                 timer_bar, level_badge)


#: 比赛截止时刻能从哪些字段看出来。数据里目前**只有时长**（`duration_min`），没有"开考时刻"，
#: 所以按「显式截止 → 开考时刻 + 时长 → 建场时刻 + 时长（仅当还没过）」的顺序找；
#: 全都找不到就交给 `ui.timer_bar` 只显示时长、页面上写「以监考指令为准」。
#: 这里**绝不"从打开页面开始倒计"**（刷新就会重算，真考场会算错——原型的 sessionStorage 就是这么错的）。
_END_FIELDS = ("end_at", "ends_at", "end_time", "deadline")
_START_FIELDS = ("start_at", "started_at", "start_time", "open_at")
_CREATED_FIELDS = ("created_at", "created")


def _ts(value) -> int:
    """把时间戳或 `"YYYY-MM-DD HH:MM:SS"` 统一成 Unix 秒；认不出来返回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else 0
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(text, fmt)))
        except ValueError:
            continue
    return 0


def _deadline_ts(contest: dict) -> int:
    """本场比赛的截止时刻（Unix 秒）。0 = 没有可用的截止时刻（计时条只显示时长）。"""
    contest = contest or {}
    for key in _END_FIELDS:
        end = _ts(contest.get(key))
        if end:
            return end
    minutes = store.duration_of(contest)          # 时长规则在 store 里
    for key in _START_FIELDS:
        start = _ts(contest.get(key))
        if start:
            return start + minutes * 60
    for key in _CREATED_FIELDS:
        created = _ts(contest.get(key))
        if not created:
            continue
        guess = created + minutes * 60
        if guess > int(time.time()):              # 只在还没过去时用这个兜底
            return guess
        break
    return 0


def _dur_text(minutes) -> str:
    """时长文案：240 → "4 小时"、210 → "3 小时 30 分钟"（`store.duration_of` 给分钟数）。"""
    try:
        m = max(0, int(minutes))
    except (TypeError, ValueError):
        m = 0
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} 小时 {m} 分钟"
    if h:
        return f"{h} 小时"
    return f"{m} 分钟"


class StudentPages:
    """学生端的所有页面（由 web.server.Handler 混入）。"""

    def _login_page(self, error: str = "", contest: dict | None = None, cookie: str = ""):
        cid = (contest or {}).get("id", "")
        head = (f'<p>本场考试：{rule_badge(contest)} {level_badge(contest)}'
                f' <b>{html.escape(contest["title"])}</b></p>'
                if contest else
                '<p class="muted">请使用老师发给你的<b>考试链接</b>进入；'
                '也可以直接输入考号（只在一场考试里出现时会自动进入）。</p>')
        body = f"""
{self._flash('err', error) if error else ''}
<div class="card">
{head}
<form method="post" action="/enter">
{f'<input type="hidden" name="c" value="{html.escape(cid)}">' if cid else ''}
<input type="text" name="kaohao" placeholder="考号（如 GD-S10029）" autofocus
       style="font-size:18px;letter-spacing:1px">
<p style="margin-top:14px"><button type="submit">进入考试</button></p>
</form>
<p class="muted">考号形如 <code>GD-S10029</code>：中间的短横是 <code>-</code>（不是 <code>_</code>），
字母要大写；数字是纯随机分配的，不用记规律。</p>
</div>
<p class="muted"><a href="/admin">管理端</a></p>"""
        self._send(page(("考试入口 · " + contest["title"]) if contest else "CSP 训练站 · 考试入口", body),
                   cookie=cookie)

    def _do_enter(self):
        form = self._form()
        kaohao = (form.get("kaohao") or "").strip().upper()
        cid = (form.get("c") or "").strip()
        if not kaohao:
            self._login_page("请填写考号。", store.get_contest(cid))
            return
        if cid:
            contest = store.get_contest(cid)
            if not contest:
                self._login_page("这个考试链接不对，请找老师核对。")
                return
            if kaohao not in store.load_roster(cid):
                self._login_page(f"考号「{kaohao}」不在本场名单里，请核对（注意大小写和中间的短横）。",
                                 contest)
                return
            self._redirect(f"/hall?c={urllib.parse.quote(cid)}",
                           cookie=f"csp={make_cookie(cid, kaohao)}; Path=/; Max-Age=43200")
            return
        # 没带考试 id：按考号找考场
        hits = store.find_contests_of_kaohao(kaohao)
        if not hits:
            self._login_page(f"考号「{kaohao}」不存在，请核对（注意大小写和中间的短横），"
                             "或者用老师给你的考试链接进入。")
            return
        if len(hits) > 1:
            links = "".join(
                f'<li><a href="/enter?c={urllib.parse.quote(c["id"])}">'
                f'{html.escape(c["title"])}</a></li>' for c in hits)
            self._send(page("要用哪个考试链接？",
                            self._flash("info", f"考号 {kaohao} 出现在多场考试里，"
                                                "请用老师给你的那场的链接进入：") +
                            f"<ul>{links}</ul>"), 409)
            return
        c = hits[0]
        self._redirect(f"/hall?c={urllib.parse.quote(c['id'])}",
                       cookie=f"csp={make_cookie(c['id'], kaohao)}; Path=/; Max-Age=43200")

    def _contests_page(self, kaohao: str, error: str = ""):
        """这个考号出现在哪些考场（考号按场次随机分配，通常只有一场）。"""
        contests = []
        for c in store.list_contests():
            entry = store.load_roster(c["id"]).get(kaohao)
            if entry:
                contests.append((c, entry, store.load_results(c["id"]).get(kaohao, {})))
        nav = (f'<p class="muted">当前考号：<b>{html.escape(kaohao)}</b>'
               f' · <a href="/help">考生须知</a>'
               f' · <a href="/logout">退出</a></p>')
        if not contests:
            self._send(page("我的考试", nav +
                            self._flash("info", "这个考号没有出现在任何考试里，请联系老师核对。")))
            return
        rows = []
        for c, entry, res in contests:
            # 公布成绩之前只说交没交；公布之后才给分数（三种赛制一视同仁）
            state = "已提交" if res else "未提交"
            if res and store.load_exam(c["id"]).get("released"):
                state = f"{store.entry_total(res)} 分"
            rows.append(
                f'<tr><td>{html.escape(c["title"])}</td><td>{rule_badge(c)}</td>'
                f'<td>{level_badge(c)}</td>'
                f'<td>{html.escape(entry.get("name", ""))}</td>'
                f'<td>{html.escape(state)}</td>'
                f'<td><a class="btn btn-sm" href="/hall?c={c["id"]}">进入</a></td></tr>')
        body = (nav + (self._flash("err", error) if error else "") +
                "<table><tr><th>考试</th><th>赛制</th><th>级别</th><th>姓名</th>"
                "<th>我的状态</th><th></th></tr>" + "".join(rows) + "</table>"
                '<p class="muted">同一个考号在不同场次是<b>不同的号</b>，'
                '所以每次都要用老师给的本场链接进入。</p>')
        self._send(page("我的考试", body))

    def _nav(self, kaohao: str, cid: str = "", *, name: str = "",
             contests: bool = False, back: bool = True, extra: str = "") -> str:
        """学生页顶部那行导航：考号（姓名） · [我的比赛] · [返回比赛] · 查成绩 · 考生须知 · 退出。

        「查成绩」是本场会话里必挂的一个入口（`/score?c=`）——踩过：这个页面存在但**没有任何
        地方链接到它**，老师公布完成绩、学生却找不到成绩页（比赛页只给"总分 N 分"一句话）。
        没公布时点进去就是一句「成绩还没有公布，请等老师通知」，也不算白点。
        「考生须知」（`/help`）同样所有学生页都挂上；「我的比赛」按原型只在比赛页出现。
        """
        q = urllib.parse.quote(cid) if cid else ""
        bits = [f'考号 <b>{html.escape(kaohao)}</b>'
                + (f'（{html.escape(name)}）' if name and name != "?" else "")]
        if contests:
            bits.append('<a href="/contests">我的比赛</a>')
        if back and cid:
            bits.append(f'<a href="/hall?c={q}">返回比赛</a>')
        if extra:
            bits.append(extra)
        if cid:
            bits.append(f'<a href="/score?c={q}">查成绩</a>')
        bits.append(f'<a href="/help{"?c=" + q if q else ""}">考生须知</a>')
        bits.append('<a href="/logout">退出</a>')
        return f'<p class="muted">{" · ".join(bits)}</p>'

    def _status_cell(self, contest: dict, exam: dict, result: dict | None, prob: dict) -> str:
        """学生看到的逐题状态。

        **零反馈**：老师公布成绩（`exam["released"]`）之前只回「已提交 / 未提交」，
        不给判定也不给分数（连"部分正确"都不给）。`ui.student_status_cell` 是管理端
        也在用的共用片段，这里包一层是为了让「公布前零反馈」这一条对学生端所有页面都生效。
        """
        if not exam.get("released"):
            got = ((result or {}).get("problems") or {}).get(
                store.problem_dir_name(int(prob["no"])))
            return ('<span class="ok">已提交</span>' if got
                    else '<span class="muted">未提交</span>')
        return student_status_cell(contest, exam, result, prob)

    def _hall_page(self, kaohao: str, contest: dict, cid: str):
        exam = store.load_exam(cid)
        result = store.load_results(cid).get(kaohao)
        name = store.student_name(cid, kaohao)
        nav = self._nav(kaohao, cid, name=name, contests=True, back=False)
        closed = not contest.get("open", True)
        body = [nav,
                f'<p>{rule_badge(contest)} {level_badge(contest)}'
                f' <b>{html.escape(contest["title"])}</b>'
                f'<span class="muted"> · {html.escape(rule_tip(contest))}</span></p>',
                timer_bar(_deadline_ts(contest), store.duration_of(contest))]
        if closed:
            # 关闭提交**不能**顺手把成绩也藏了：老师考完 = 关提交 + 公布成绩，
            # 这是最常见的顺序。以前这里直接 return，学生只看到"已关闭提交"，
            # 公布了的分数一个字都不显示（踩过）。
            # 看题、看题面、看大样例都不受影响，关的只是"交"（提交区见 `_submit_block`）。
            body.append(self._flash("err", "本场比赛已关闭提交。"))
        if result:
            at = html.escape(str(result.get("submitted_at") or ""))
            if not exam.get("released"):
                # 零反馈：只说交没交（不提判定/分数，也不提"正在判分"）
                body.append(self._flash("ok", f"已提交（{at}）。"
                                             + ("" if closed else "可以反复提交，离场前确认交的是最终版本。")))
            else:
                # 注意：`_flash()` 会对正文做 html.escape，所以链接**不能**塞进 flash 里
                # （踩过：页面上直接显示出了 `<a href=...>` 这串字面量）。分两条拼。
                score_url = (f'/score?c={urllib.parse.quote(cid)}'
                             f'&kaohao={urllib.parse.quote(kaohao)}')
                body.append(self._flash("info", f"你的总分 {store.entry_total(result)} 分。")
                            + f'<p style="margin:-2px 0 6px">'
                              f'<a class="btn btn-sm btn-gray" href="{score_url}">'
                              f'看逐题得分 →</a></p>')
        body.append("<h2>本场题目</h2>")
        body.append(self._hall_problems(exam, contest, cid))
        body.append(self._submit_block(exam, contest, cid, kaohao, name))
        self._send(page(contest["title"], "".join(body)))

    def _hall_problems(self, exam: dict, contest: dict, cid: str) -> str:
        """本场题目表：题号 / **英文名** / 题目 / [你要读写的文件] / 大样例 / 满分。

        英文名列用 `store.code_of`（老师配题时填的，学生拿它当文件夹名和源文件名，
        如 `candy`），文件列只在需要 freopen 的赛制下出现（`<英文名>.in / <英文名>.out`）。
        学生侧看不到「题目编号」（`T00001` 那种）——那是老师定位题目用的。
        """
        probs = exam.get("problems", [])
        if not probs:
            return '<div class="warn">管理员还没有为本场比赛配置题目。</div>'
        show_files = bool(store.rule_of(contest).get("freopen"))
        head = ("<tr><th>题号</th><th>题目英文名</th><th>题目（点开看题面）</th>"
                + ("<th>你要读写的文件</th>" if show_files else "")
                + "<th>大样例</th><th>满分</th></tr>")
        rows = []
        for p in probs:
            code = store.code_of(p)
            title = str(p.get("title") or p.get("pid") or "")
            if cid:
                link = (f'<a href="/problem?c={urllib.parse.quote(cid)}'
                        f'&p={urllib.parse.quote(str(p["no"]))}">'
                        f'{html.escape(title) or "查看题面"}</a>')
            else:
                link = html.escape(title)
            cells = [f'第 {p["no"]} 题', f'<b>{html.escape(code)}</b>', link]
            if show_files:
                cells.append(f'{html.escape(code)}.in / {html.escape(code)}.out')
            cells.append(sample_link(cid, p["no"]) if (cid and has_samples(str(p.get("pid") or "")))
                         else '<span class="muted">—</span>')
            cells.append(str(p.get("full", 100)))
            rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        tip = ('<div class="card" style="padding:12px 15px;margin-bottom:10px">'
               '<b>每题的名字用表里的「英文名」，照抄就行</b>'
               '<span class="muted">——文件夹名、源文件名'
               + ('、freopen 的文件名' if show_files else '')
               + '都用它。别自己改名、也别用题号（第 1 题不等于 <code>1</code>）。</span></div>')
        return tip + f"<table>{head}{''.join(rows)}</table>"

    def _dir_tree(self, exam: dict, kaohao: str, name: str = "") -> str:
        """广东考区要求的目录结构示意：考号目录 + 个人信息文件 + 每个英文名一个子目录。

        不用 `ui.folder_tree`：它没有个人信息文件那一行，首行还带着旧考号格式的举例
        （`例如 GD-0001`），与本轮口径不符。
        """
        probs = exam.get("problems", [])
        lines = [f"{kaohao}/                 ← 考号目录（文件夹名必须是你的考号，建在盘符根目录下）", ""]
        if name and name != "?":
            lines.append(f"├── {name}.txt               ← 个人信息文件（文件名＝你的名字）")
        if not probs:
            lines.append("├── <英文名>/")
            lines.append("│   └── <英文名>.cpp")
        for i, p in enumerate(probs):
            code = store.code_of(p)
            last = i == len(probs) - 1
            lines.append(f"{'└── ' if last else '├── '}{code}/")
            lines.append(f"{'    ' if last else '│   '}└── {code}.cpp            "
                         f"← 第 {p['no']} 题的代码")
        return f'<div class="tree">{html.escape(chr(10).join(lines))}</div>'

    def _rule_warn(self, kaohao: str, exam: dict) -> str:
        """命名红线提示框（广东考区）：`_` 与 `-`、大小写、空格、三层名字一致。"""
        prefix, level, digits = store.split_kaohao(kaohao)
        wrong = f"{prefix}_{level}{digits}" if level else "GD_S48213"
        probs = exam.get("problems", [])
        code = store.code_of(probs[0]) if probs else "candy"
        return (f'<div class="rule-warn">'
                f'<b>命名红线：写错一个字符，这一题就是 0 分</b><ul>'
                f'<li><code>_</code> 和 <code>-</code> 是两个字：'
                f'<code>{html.escape(kaohao)}</code> 不能写成 <code>{html.escape(wrong)}</code></li>'
                f'<li><b>严格区分大小写</b>：<code>{html.escape(code)}</code> 不能写成 '
                f'<code>{html.escape(code.upper())}</code>；源文件后缀要小写 <code>.cpp</code></li>'
                f'<li>文件夹名<b>不能有空格</b>（包括前后）</li>'
                f'<li>三层名字要一致：考号文件夹 → 题目英文名子文件夹 → 英文名.cpp</li>'
                f'</ul></div>')

    def _person_card(self, name: str) -> str:
        """个人信息文件提示（广东考区强制要求，不参与评分）。"""
        known = bool(name) and name != "?"
        want = (f'本场应提交：<span class="me-name">{html.escape(name)}.txt</span> ' if known else "")
        return (f'<div class="me-card"><b>别忘了交个人信息文件</b><br>'
                f'在考号文件夹下放一个以<b>你自己名字</b>命名的文本文件，内容写：'
                f'姓名、性别、年级、地区、学校、辅导老师、提交的程序。<br>'
                f'{want}<span class="muted">（这个文件不计分，但广东考区要求必须有）</span></div>')

    def _submit_block(self, exam: dict, contest: dict, cid: str, kaohao: str, name: str) -> str:
        """提交区：三种赛制**统一**交考号文件夹（上传文件夹或 zip），不再有逐题提交按钮。

        赛制差异只剩「程序怎么写」：CSP 要 freopen 读写编号文件，OI/IOI 用标准输入输出。

        **老师关了提交就只给一句提示，不摆表单**：服务端 `/upload` 本来就拒收
        （见 `_do_upload` 的 `open` 判断），但页面上还摆着"选文件夹 + 提交我的代码"
        会让人以为能交、白点一次。看题、看题面、看大样例不受影响 —— 关的只是"交"。
        """
        if not contest.get("open", True):
            return ('<h2>提交我的文件夹</h2><div class="card">'
                    '<p class="muted" style="margin:0">本场提交已关闭，不能再交/改交。</p>'
                    '<p class="muted" style="margin:6px 0 0">题目和题面照常可以看。</p>'
                    '</div>')
        rule = store.rule_of(contest)
        q = urllib.parse.quote(cid)
        parts = ['<h2>提交我的文件夹</h2><div class="card">']
        if rule.get("freopen"):
            parts.append(self._person_card(name))
            parts.append(self._dir_tree(exam, kaohao, name))
            parts.append(self._rule_warn(kaohao, exam))
            tail = ("文件夹名或 freopen 文件名写错，这题就读不到数据、按 0 分算"
                    "（交完会给一份检查清单，离场前逐条核对）")
            pick_hint = f'选<b>你考号那个文件夹</b>（整个 <code>{html.escape(kaohao)}</code> 目录）即可'
        else:
            parts.append(self._dir_tree(exam, kaohao, name))
            parts.append(
                f'<div class="flash flash-info" style="margin-top:10px">'
                f'<b>本场是 {html.escape(rule["name"])}，用标准输入输出</b>：程序里用 '
                f'<code>cin</code>/<code>scanf</code> 读、<code>cout</code>/<code>printf</code> 写，'
                f'<b>不需要 freopen</b>（那是 CSP 赛制的要求）。文件夹只是用来区分每题的文件。'
                f'</div>')
            parts.append(self._person_card(name))
            tail = "交完会给你一份检查清单，逐条核对再离场"
            pick_hint = (f'选<b>你考号那个文件夹</b>（整个 <code>{html.escape(kaohao)}</code> 目录）'
                         f'即可；也可以只选几个 <code>.cpp</code> 文件')
        parts.append(f"""<form method="post" action="/upload?c={q}" enctype="multipart/form-data"
      data-submit-guard>
<p style="margin-top:12px"><input type="file" name="files" webkitdirectory directory multiple
          style="padding:10px;background:#f8fafc;border:1px dashed #94a3b8;border-radius:8px;width:100%"></p>
<p class="muted">{pick_hint}，或打包成 zip：
  <input type="file" name="zipfile" accept=".zip"></p>
<p style="margin-top:12px"><button type="submit">提交我的代码</button>
<span class="muted">{tail}</span></p>
</form>
</div>""")
        return "".join(parts)

    def _receipt_page(self, kaohao: str, contest: dict, cid: str, files: dict,
                      picked: dict, missing: list) -> None:
        """交卷回执：**只查文件名与目录结构**，不涉及算法对错。

        检查项来自 core/wrapper（T-05）：`pick_sources()` 的 (picked, missing) 给出"哪题用哪个
        文件、哪题没找到、大小写或文件名有什么毛病"，`find_person_file()` 给个人信息文件交了没，
        `check_freopen()` 在需要 freopen 的赛制下核对读写文件名。
        老师公布成绩之前**不给任何判分信息**（连"正在判分"都不提）。
        """
        exam = store.load_exam(cid)
        rule = store.rule_of(contest)
        name = store.student_name(cid, kaohao)
        strict = True                # 与 wrapper.pick_sources 的口径一致：严格区分大小写
        probs = exam.get("problems", [])
        missing = [str(m) for m in (missing or [])]
        # wrapper 的个人信息文件提示单独拿出来（它可能带"差大小写"这类细节）
        person_hint = next((m for m in missing if m.startswith("缺个人信息文件")), "")
        rest = [m for m in missing if not m.startswith("缺个人信息文件")]
        nav = self._nav(kaohao, cid, name=name)

        def item(ok: bool, text: str, note: str = "") -> str:
            note_html = f'<br><span class="ck-note">{note}</span>' if note else ""
            return (f'<li class="{"pass" if ok else "fail"}">'
                    f'<span class="ck">{"✓" if ok else "✕"}</span>'
                    f'<span>{text}{note_html}</span></li>')

        items: list[str] = []
        # ① 考号文件夹：真实考场交的就是整个考号目录
        def is_kaohao_head(rel) -> bool:
            head = str(rel).replace("\\", "/").lstrip("/").split("/")[0]
            return head == kaohao if strict else head.lower() == kaohao.lower()
        if any(is_kaohao_head(rel) for rel in files):
            items.append(item(True, f"考号文件夹名正确：<code>{html.escape(kaohao)}</code>"))
        else:
            items.append(item(False,
                              f"没检测到以考号命名的文件夹 <code>{html.escape(kaohao)}/</code>",
                              f"提交的文件应放在 {html.escape(kaohao)} 目录里；"
                              f"建议重选整个考号文件夹再交一次"))
        # ② 逐题：哪题没交、哪题的读写文件（freopen）没写对
        for p in probs:
            no = int(p["no"])
            code = store.code_of(p)
            rel = (picked or {}).get(no)
            if not rel:
                items.append(item(False, f"第 {no} 题没找到代码文件",
                                  f"应放在 <code>{html.escape(code)}/{html.escape(code)}.cpp</code>"))
                continue
            hint = ""
            if rule.get("freopen"):
                hint = wrapper.check_freopen(decode_text(files.get(rel) or b""), code)
            if hint:
                items.append(item(False, f"第 {no} 题 <code>{html.escape(rel)}</code> 文件名正确",
                                  html.escape(hint)))
            else:
                ok_extra = (f'，检测到 <code>freopen("{html.escape(code)}.in")</code>'
                            if rule.get("freopen") else "")
                items.append(item(True, f"第 {no} 题 <code>{html.escape(rel)}</code> 结构正确{ok_extra}"))
        # ③ 命名大小写（严格模式下 wrapper 会点出来）
        for m in [m for m in rest if "大小写" in m]:
            items.append(item(False, "命名大小写有问题", html.escape(m)))
        # ④ 个人信息文件（wrapper 找到的"差大小写"之类的细节就在它的提示里，直接引用）
        if name and name != "?":
            person = wrapper.find_person_file(list(files), name, strict=strict)
            if person:
                items.append(item(True, f"个人信息文件：<code>{html.escape(person)}</code>"))
            else:
                items.append(item(False, f"没找到个人信息文件 <code>{html.escape(name)}.txt</code>",
                                  html.escape(person_hint) or
                                  "广东考区要求必须有：考号目录下放一个以本人姓名命名的 txt"
                                  "（不参与判分，内容写姓名/性别/年级/地区/学校/辅导老师/提交的程序）"))
        # wrapper 给的其它说明（题号归属、语言支持之类）原样列出，页面不再解析它的措辞
        others = [m for m in rest if "大小写" not in m]

        rows = []
        for p in probs:
            no = int(p["no"])
            rel = (picked or {}).get(no) or ""
            cell = (f'<a href="/file?c={urllib.parse.quote(cid)}&f={urllib.parse.quote(rel)}">'
                    f'{html.escape(rel)}</a>' if rel else '<span class="muted">—</span>')
            rows.append(f'<tr><td>第 {no} 题</td><td><b>{html.escape(store.code_of(p))}</b></td>'
                        f'<td>{cell}</td></tr>')
        warn_html = (f'<div class="warn" style="margin-top:8px">'
                     + "<br>".join(html.escape(m) for m in others) + "</div>") if others else ""

        flash = self._flash("ok", f"收到你交的 {len(files)} 个文件。下面先核对文件名和目录结构；"
                                  "考试期间不显示任何判分信息（没有编译结果、没有测试点、"
                                  "没有分数），成绩由老师在考试结束后统一公布。")
        nxt = ["可以<b>反复提交</b>，每道题以<b>最后一次</b>提交的代码为准——离场前确认交的是最终版本。",
               "改了代码就<b>重新交一次整个考号文件夹</b>，不要只交单个文件。",
               "考试期间不显示编译信息、测试点和分数，交完就等老师公布；"
               "公布后可在「查成绩」页逐题查看。"]
        body = (nav + flash +
                '<h2>文件结构检查</h2><div class="card">'
                '<p class="muted" style="margin-top:0">下面这些只检查<b>文件名和目录位置</b>，'
                '不涉及你的算法对错。真实考场不会有人帮你查，离场前一定自己核一遍。</p>'
                f'<ul class="checklist">{"".join(items)}</ul></div>'
                '<h2>判分用的文件</h2><div class="card">'
                '<table><tr><th>题号</th><th>题目英文名</th><th>判分用的文件</th></tr>'
                + "".join(rows) + warn_html + '</div>'
                '<h2>接下来</h2><div class="card"><ul style="margin:0 0 0 20px">'
                + "".join(f"<li>{x}</li>" for x in nxt) + "</ul>"
                + f'<p style="margin-top:12px"><a class="btn" href="/hall?c={urllib.parse.quote(cid)}">'
                  f'返回比赛</a> <a class="btn btn-gray" href="/help?c={urllib.parse.quote(cid)}">'
                  f'考生须知</a></p></div>')
        self._send(page("已提交", body))

    def _help_page(self, cid: str = ""):
        """考生须知：整页就是**考区官方通告的原文**（`config.NOTICE_DOC` 那份 Markdown）。

        通告跟着考区、年份变，所以通篇内容都在那个 Markdown 文件里 —— 换一年换一份
        文件就行，页面代码不掺任何口径。文件缺失时只给一句「以考点下发的纸质通告为准」
        加原文链接，不会 500。

        通告之后附「本场信息」：考号、目录树、个人信息文件名、提交方式。这几项是
        **本平台按这一位学生算出来的**，通告里只有 GD-S00001／张三 那种通用示例，
        所以不能拿通告顶掉。通告本身讲清楚的（目录结构、命名红线、输入输出、
        NOI Linux 编译坑、考场纪律）页面不再重复。

        未登录也能看（考前先读一遍），会话属于本场时导航里带上考号姓名。
        """
        session = self._session()
        if not cid and session:
            cid = session[0]
        contest = store.get_contest(cid) if cid else None
        if not contest:
            self._send(page("没有这场考试",
                            self._flash("err", "考试链接不对，请找老师核对。") +
                            '<p><a class="btn" href="/">回到首页</a></p>'), 404)
            return
        kaohao = self._student(cid) or ""
        name = store.student_name(cid, kaohao) if kaohao else ""
        exam = store.load_exam(cid)
        rule = store.rule_of(contest)
        level = store.level_of(contest)
        minutes = store.duration_of(contest)
        probs = exam.get("problems", [])
        total = sum(int(p.get("full") or 0) for p in probs)
        prefix = str(contest.get("prefix") or store.DEFAULT_PREFIX)
        tree_kaohao = kaohao or f"{prefix}-{level}00001"
        tree_name = name if name and name != "?" else ""
        # 本场应提交的个人信息文件名（不知道姓名时只给个示意）
        person_name = f"{html.escape(name)}.txt" if name and name != "?" else "你的名字.txt"
        score_text = "已公布" if exam.get("released") else "赛中不显示，赛后由老师公布"
        nav = (self._nav(kaohao, cid, name=name) if kaohao else
               f'<p class="muted"><a href="/enter?c={urllib.parse.quote(cid)}">考试入口</a>'
               f' · <a href="/">首页</a> · <a href="/contests">我的比赛</a></p>')

        if os.path.isfile(NOTICE_DOC):
            notice_html = ('<div class="card stmt" style="overflow:auto">'
                           + md_to_html(open(NOTICE_DOC, encoding="utf-8").read())
                           + '</div>')
        else:
            notice_html = (
                '<div class="card"><p class="muted" style="margin-bottom:0">'
                '本机没有放考区通告原文（<code>notice_guangdong.md</code> 缺失），'
                '请以考点下发的纸质通告为准，原文见 '
                '<a href="https://www.noi.cn/gs/xw/gd/" target="_blank">NOI 官网 · 广东</a>。'
                '</p></div>')

        body = f"""{nav}
{self._flash('info', '开考前请先读完这一页：下面是考区官方通告的原文。')}

{notice_html}

<h2>附：本场信息</h2>
<div class="card">
<div class="kv">
  <div><b>你的考号</b>{html.escape(kaohao) if kaohao else "（还没登录，登录后这里显示你自己的号）"}</div>
  <div><b>个人信息文件</b>本场应提交 <code>{person_name}</code></div>
  <div><b>赛制</b>{rule_badge(contest)} {html.escape(store.level_name(level))}</div>
  <div><b>时长 / 满分</b>{html.escape(_dur_text(minutes))} / {total} 分（{len(probs)} 题）</div>
  <div><b>提交方式</b>{html.escape(store.SUBMIT_UPLOAD_NOTE)}</div>
  <div><b>成绩</b>{html.escape(score_text)}</div>
</div>
<p class="muted" style="margin-top:10px">考号由本平台随机发号（{store.DEFAULT_WIDTH} 位随机数，
<b>不</b>是从 1 顺着排的），请以准考证为准 —— <b>不是机号</b>。</p>
{self._dir_tree(exam, tree_kaohao, tree_name or "你的名字")}
{self._rule_warn(kaohao or tree_kaohao, exam)}
{self._person_card(name)}
<p class="muted" style="margin-bottom:0">考试期间看不到任何判分信息（编译结果、测试点、
分数都没有），成绩由老师在考试结束后统一公布 ——
所以交之前自己核对一遍：<b>文件名、目录位置、freopen 的文件名</b>。
考试页右上角和交卷回执都会帮你核对目录结构。</p>
</div>"""
        self._send(page(f"考生须知 · {contest['title']}", body))

    def _submit_page(self, kaohao: str, contest: dict, cid: str, pno: str,
                     flash: str = "", code: str = "", lang: str = ""):
        """历史入口（网页粘贴代码）。三种赛制现在统一交考号文件夹，这里直接回比赛页。"""
        self._redirect(f"/hall?c={urllib.parse.quote(cid)}")

    def _do_submit_code(self):
        """历史入口（`POST /submit`）：同上，交卷一律走 `POST /upload`（上传文件夹）。"""
        cid = self._query().get("c", "")
        self._redirect(f"/hall?c={urllib.parse.quote(cid)}" if cid else "/")

    def _do_upload(self):
        q = self._query()
        cid = q.get("c", "")
        kaohao = self._student(cid)
        if not kaohao:
            self._redirect(f"/enter?c={urllib.parse.quote(cid)}" if cid else "/")
            return
        contest = store.get_contest(cid)
        exam = store.load_exam(cid)
        if not contest:
            self._send(page("无法提交", self._flash("err", "比赛不存在。")), 400)
            return
        if not contest.get("open", True):
            self._send(page(contest["title"], self._flash("err", "本场比赛已关闭提交。")))
            return

        try:
            files = self._read_upload()
        except ValueError as e:
            self._send(page("提交失败", self._flash("err", str(e)) +
                            f'<p><a href="/hall?c={cid}">返回重试</a></p>'), 400)
            return
        if not files:
            self._send(page("提交失败", self._flash("err", "没有收到文件，请重新选择文件夹。") +
                            f'<p><a href="/hall?c={cid}">返回重试</a></p>'), 400)
            return

        base = store.upload_dir(cid, kaohao)
        if os.path.isdir(base):
            for root, dirs, fns in os.walk(base, topdown=False):
                for fn in fns:
                    try:
                        os.remove(os.path.join(root, fn))
                    except OSError:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass
        for rel, data in files.items():
            safe = os.path.normpath(rel).replace("\\", "/").lstrip("/")
            if ".." in safe.split("/"):
                continue
            dst = os.path.join(base, safe)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)

        # 不做目录结构校验：尽量宽松地把文件对上题目，找不到就当作这道题没交（自然是 0 分）。
        # 个人信息文件与大小写都在 core/wrapper 里判：一律严格区分大小写（贴近真实考场）。
        picked, missing = wrapper.pick_sources(
            list(files.keys()), exam.get("problems", []),
            student_name=store.student_name(cid, kaohao),
            strict=True)
        result = store.load_results(cid).get(kaohao, {})
        result.update({
            "picked": {str(k): v for k, v in picked.items()},
            "missing_sources": missing,
            "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "problems": result.get("problems", {}),
            "judging": False,
        })
        store.put_result(cid, kaohao, result)
        log(f"[{cid}/{kaohao}] 收到 {len(files)} 个文件，匹配到 {len(picked)} 道题，开始判分")
        # 判分照常跑（老师要看成绩），只是学生端在老师公布成绩之前看不到
        threading.Thread(target=grading.safe_judge_csp, args=(cid, kaohao, picked, exam), daemon=True).start()
        self._receipt_page(kaohao, contest, cid, files, picked, missing)

    def _serve_submit_file(self, cid: str, kaohao: str, rel: str, *,
                           download: bool = False, back: str = "", from_admin: bool = False):
        """预览/下载某个学生提交的文件（代码直接看，二进制只给下载）。

        rel 是相对 uploads/<考号>/ 的路径；会做路径校验，不允许跳出该目录。
        """
        rel = urllib.parse.unquote(rel or "").replace("\\", "/").lstrip("/")
        base = store.upload_dir(cid, kaohao)
        safe = os.path.normpath(rel)
        if not rel or safe.startswith("..") or os.path.isabs(safe):
            self._send(page("文件不存在", self._flash("err", "文件路径不对。")), 400)
            return
        path = os.path.join(base, safe)
        if not os.path.isfile(path):
            self._send(page("文件不存在",
                            self._flash("err", f"服务器上没有这个文件：{safe}")), 404)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self._send(page("读不到文件", self._flash("err", str(e))), 500)
            return

        if download:                     # 下载：原样吐字节
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            fname = urllib.parse.quote(os.path.basename(safe))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")
            self.end_headers()
            self.wfile.write(data)
            return

        result = store.load_results(cid).get(kaohao) or {}
        picked = result.get("picked") or result.get("structure_picked") or {}
        exam = store.load_exam(cid)
        used_by = ""
        prob = next((p for p in exam.get("problems", []) if picked.get(str(p.get("no"))) == safe), None)
        if prob:
            used_by = f'第 {prob["no"]} 题（{store.code_of(prob)}）'
        dl = (f'&dl=1')
        here = (f'/admin/file{cid_query("", cid, kaohao)}&f={urllib.parse.quote(safe)}'
                if from_admin else
                f'/file?c={urllib.parse.quote(cid)}&f={urllib.parse.quote(safe)}')
        title = os.path.basename(safe)
        head = (f'<p class="muted">{"管理端 · " if from_admin else ""}'
                f'考号 <b>{html.escape(kaohao)}</b>'
                f'（{html.escape(store.student_name(cid, kaohao))}）'
                f' · <a href="{html.escape(back or "/hall?c=" + cid)}">返回</a>'
                + (f' · <a href="{html.escape(here + dl)}">下载文件</a>' if not from_admin else '')
                + "</p>")
        if from_admin:
            head = (head.replace("返回", "返回提交详情")
                    .replace(f'href="{html.escape(back or "/hall?c=" + cid)}"',
                             f'href="{html.escape(back or "/admin")}"')
                    + f'<p><a class="btn btn-sm btn-gray" href="{html.escape(here + dl)}">下载文件</a></p>')
        info = (f'<div class="kv"><div><b>文件</b><code>{html.escape(safe)}</code></div>'
                f'<div><b>大小</b>{_fmt_bytes(len(data))}</div>'
                f'<div><b>判分用</b>{html.escape(used_by) or "没用到"}</div></div>')
        if looks_binary(data):
            body = (head + f'<h2>{html.escape(title)}</h2><div class="card">{info}'
                    + '<p class="warn">这是二进制文件（.exe/.o 之类），不能在这里预览。</p></div>')
        else:
            body = (head + f'<h2>{html.escape(title)}</h2><div class="card">{info}'
                    + code_pre(decode_text(data)) + "</div>")
        self._send(page(f"文件 · {title}", body))

    def _read_upload(self) -> dict[str, bytes]:
        fields, files, _file_fields = self._parse_multipart(MAX_UPLOAD)
        out: dict[str, bytes] = {}
        for name, data in files.items():
            if name.lower().endswith(".zip"):
                out.update(_read_zip(data))
            else:
                out[name.replace("\\", "/")] = data
        return out

    def _score_query_page(self, kaohao_input: str, contest: dict | None = None, cid: str = ""):
        if contest is None:
            self._send(page("查成绩", self._flash("info", "请从比赛里查看成绩。") +
                            '<p><a class="btn" href="/contests">我的比赛</a></p>'))
            return
        exam = store.load_exam(cid)
        # 会话属于本场时，用会话里的考号做导航（原型的查成绩页也是这样）
        me = self._student(cid) or kaohao_input.strip().upper()
        # 零反馈：老师公布成绩之前一律不给分数（三种赛制一视同仁）
        if not exam.get("released"):
            self._send(page("查成绩", self._flash("info", "成绩还没有公布，请等老师通知。") +
                            f'<p><a class="btn" href="/hall?c={cid}">返回比赛</a></p>'))
            return
        out = [self._nav(me, cid, name=store.student_name(cid, me)) if me else "",
               f'<form method="get" action="/score"><input type="hidden" name="c" value="{cid}">'
               '<p>输入考号查成绩：</p>'
               f'<input type="text" name="kaohao" value="{html.escape(kaohao_input)}">'
               '<p style="margin-top:12px"><button type="submit">查询</button></p></form>']
        if kaohao_input:
            k = kaohao_input.strip().upper()
            res = store.load_results(cid).get(k)
            if k not in store.load_roster(cid) or not res:
                out.append(self._flash("err", f"没有找到考号 {k} 的提交记录。"))
            else:
                rows = "".join(
                    f'<tr><td>{html.escape(store.code_of(p))}</td>'
                    f'<td>{self._status_cell(contest, exam, res, p)}</td></tr>'
                    for p in exam.get("problems", []))
                out.append(f'<div class="card"><p><b>{html.escape(store.student_name(cid, k))}</b>（{k}）</p>'
                           f'<table><tr><th>题目</th><th>得分</th></tr>{rows}</table>'
                           f'<p style="font-size:20px;margin-top:12px"><b>总分：{store.entry_total(res)}</b></p></div>')
        self._send(page("查成绩", "".join(out)))

    def _problem_page(self, kaohao: str, contest: dict, cid: str, pno: str):
        """看题面（Markdown 从评测站读，带本地缓存）。

        题面里**不写**"本题的读写文件与提交方式"（与真实题面一致）：要求统一放在考生须知里。
        """
        exam = store.load_exam(cid)
        prob = next((p for p in exam.get("problems", []) if str(p.get("no")) == str(pno)), None)
        if not prob:
            self._send(page("题目不存在", self._flash("err", "题目不对。")), 404)
            return
        pid = str(prob.get("pid") or "")
        slug = store.code_of(prob)
        full = int(prob.get("full", 100))
        cache_dir = os.path.join(store.DATA_DIR, "statements")
        try:
            text = hydro.problem_statement(pid, cache_dir)
        except Exception as e:                     # noqa: BLE001
            log(f"[题面] 读取 {pid} 失败：{e}")
            text = ""
        nav = self._nav(kaohao, cid, name=store.student_name(cid, kaohao),
                        extra=(f'<a href="/sample?c={cid}&p={prob["no"]}">下载大样例</a>'
                               if has_samples(pid) else ""))
        head = (f'<p>{rule_badge(contest)} {level_badge(contest)}'
                f' <b>{html.escape(contest["title"])}</b></p>'
                f'<h2>第 {prob["no"]} 题 · {html.escape(slug)}'
                f'<span class="muted"> {html.escape(str(prob.get("title") or ""))}'
                f' · 满分 {full}</span></h2>')
        if not text.strip():
            body = (nav + head + self._flash("err", "这道题还没有题面（老师还没写/还没同步过来）。"))
        else:
            body = nav + head + f'<div class="card stmt">{md_to_html(text)}</div>'
        self._send(page(f"题面 · {slug}", body, math=True))

    def _sample_page(self, kaohao: str, contest: dict, cid: str, pno: str, want: str = ""):
        """学生看/下载本场某题的大样例（模拟 CSP 里可以查看大样例）。"""
        exam = store.load_exam(cid)
        prob = next((p for p in exam.get("problems", []) if str(p.get("no")) == str(pno)), None)
        if not prob:
            self._send(page("题目不存在", self._flash("err", "题目不对。")), 404)
            return
        pid = str(prob.get("pid") or "")
        slug = store.code_of(prob)
        nav = self._nav(kaohao, cid, name=store.student_name(cid, kaohao))
        if want:      # 下载某个文件
            path = make_problem.sample_path(pid, want) if make_problem else None
            if not path:
                self._send(page("文件不存在", self._flash("err", "找不到这个样例文件。")), 404)
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            fname = urllib.parse.quote(os.path.basename(path))
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{fname}")
            self.end_headers()
            self.wfile.write(data)
            return
        info = make_problem.load_samples(pid) if make_problem else {}
        if not info.get("items"):
            self._send(page("大样例", nav + self._flash("err", "这道题没有提供大样例文件。")), 404)
            return
        blocks = []
        for it in info["items"]:
            kind = "大样例" if it.get("kind") == "big" else "题目样例"
            rows = []
            for f in it["files"]:
                role = {"in": "输入", "out": "答案", "other": "附件"}.get(f.get("role"), "文件")
                size = _fmt_bytes(f.get("size") or 0)
                url = f"/sample?c={urllib.parse.quote(cid)}&p={urllib.parse.quote(str(pno))}&f={urllib.parse.quote(f['name'])}"
                rows.append(f'<tr><td>{html.escape(f["name"])}</td><td>{role}</td>'
                            f'<td class="muted">{size}</td>'
                            f'<td><a class="btn btn-sm" href="{url}">下载</a></td></tr>')
            blocks.append(
                f'<div class="card"><h2 style="margin-top:0">{kind}'
                f'<span class="muted"> {html.escape(it.get("label", ""))}</span></h2>'
                f'<table><tr><th>文件</th><th>用途</th><th>大小</th><th></th></tr>'
                f'{"".join(rows)}</table></div>')
        body = (nav +
                f'<p>{rule_badge(contest)} {level_badge(contest)}'
                f' <b>{html.escape(contest["title"])}</b>'
                f' · 第 {prob["no"]} 题 <b>{html.escape(slug)}</b>'
                f'<span class="muted"> {html.escape(str(prob.get("title") or ""))}</span></p>'
                f'<p class="muted">大样例供本机调试，不参与评测。'
                f'<b>别把 <code>.in</code> / <code>.out</code> 留在要交的文件夹里</b>——'
                f'考场要求只交源码。</p>'
                + "".join(blocks))
        self._send(page(f"大样例 · {slug}", body))
