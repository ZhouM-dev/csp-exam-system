"""页面外壳与共用 HTML 片段（CSS、导航、表格、徽章、目录树、题面样式）

只做展示，不含业务规则；规则在 core/ 里。"""

from __future__ import annotations


import os
import html
import time
import urllib.parse

from ..core import problems as make_problem, wrapper
from ..core.grading import graded_cell

from ..core import store
from ..core.util import _fmt_bytes, _fmt_ms, _fmt_kb
from ..core.markdown import md_to_html  # noqa: F401  (导出给页面用)
from .tabler_theme import document

# Exam-specific widget CSS lives in static/exam-components.css.


def render_upload_tree_html(base: str, notes: dict[str, str] | None = None,
                            link_fn=None) -> tuple[str, int, int]:
    """和 render_upload_tree 一样，但**文件名是可点的链接**（点开预览/下载）。

    link_fn(相对路径) -> href；不传就退化成纯文本树。
    返回 (树 HTML, 文件数, 总字节)。
    """
    files: list[tuple[str, int]] = []
    for root, dirs, fns in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in fns:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, base).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            files.append((rel, size))
    if not files:
        return "", 0, 0
    files.sort()
    tree: dict = {}
    for rel, size in files:
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = size

    notes = notes or {}
    junk = tuple(wrapper.JUNK_EXTS)
    out = []

    def walk(node: dict, prefix: str, path: str) -> None:
        items = sorted(node.items(), key=lambda kv: (isinstance(kv[1], int), str(kv[0])))
        for i, (name, val) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            full = f"{path}/{name}" if path else name
            if isinstance(val, int):
                tags = []
                if notes.get(full):
                    tags.append(f"← {notes[full]}")
                elif name.lower().endswith(junk):
                    tags.append("← 多余文件（编译产物/数据）")
                elif full not in notes and notes:
                    tags.append("← 没用到")
                shown = (f'<a href="{html.escape(link_fn(full), quote=True)}">{html.escape(name)}</a>'
                         if link_fn else html.escape(name))
                out.append(f'{html.escape(prefix + branch)}{shown}'
                           f'<span class="muted">   ({_fmt_bytes(val)})</span>'
                           + (f'<span class="muted">  {" ".join(tags)}</span>' if tags else ""))
            else:
                out.append(f"{html.escape(prefix + branch)}{html.escape(name)}/")
                walk(val, prefix + ("    " if last else "│   "), full)

    walk(tree, "", "")
    return ("<pre class=\"tree tree-link\">" + "\n".join(out) + "</pre>",
            len(files), sum(s for _, s in files))


def looks_binary(data: bytes) -> bool:
    """粗略判断是不是二进制（有 NUL / 控制字符多 / 两种编码都解不出来）。"""
    if not data:
        return False
    head = data[:4096]
    if b"\x00" in head:
        return True
    if sum(1 for b in head if b < 9 or (13 < b < 32)) > max(4, len(head) // 20):
        return True
    for enc in ("utf-8", "gbk"):
        try:
            data.decode(enc)
            return False
        except UnicodeDecodeError:
            continue
    return True


def decode_text(data: bytes) -> str:
    """尽量用 UTF-8，不行再试 GBK。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def code_pre(text: str, with_lines: bool = True, limit: int = 2 * 1024 * 1024) -> str:
    """把文本渲染成代码块；带行号（方便老师点行讲评）。太大就不展开，让人下载。"""
    raw = text.encode("utf-8", "replace")
    if len(raw) > limit:
        return (f'<div class="warn">文件较大（{_fmt_bytes(len(raw))}），'
                f'不在页面里展开，请点「下载」查看。</div>')
    if not with_lines:
        return f'<pre class="code-view">{html.escape(text)}</pre>'
    # **行号单独一列**（不是拼进代码里）：这样「复制代码」拿到的是纯代码，
    # 不带行号（踩过：行号拼在同一个 <pre> 里，一复制就把 `  1  ` 也粘走了）。
    # 两列都是 <pre>，同样的字号/行高，所以对得齐。
    lines = text.splitlines() or [""]
    width = max(2, len(str(len(lines))))
    gutter = "\n".join(f"{i:>{width}}" for i in range(1, len(lines) + 1))
    return ('<div class="code-flex">'
            f'<pre class="code-gutter">{gutter}</pre>'
            f'<pre class="code-view code-numbered code-body">{html.escape(text)}</pre>'
            '</div>')


#: 共用前端脚本（倒计时 / 悬浮窗）。放在 static/ 下，page() 把它内联进页面。
CONTEST_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "static", "contest.js")
_contest_js_cache: str | None = None


def contest_script() -> str:
    """`static/contest.js` 的源码。

    **为什么内联而不是 `<script src>`**：线上 `/static/` 只路由了 katex
    （server.py 不在本任务的改动范围），内联不依赖静态路由、少一次请求。
    以后 server.py 里加了 `/static/` 路由，可以换成
    `<script src="/static/contest.js">`，文件还是同一份。
    """
    global _contest_js_cache
    if _contest_js_cache is None:
        try:
            with open(CONTEST_JS_PATH, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:                     # 文件没部署上也不该让整站打不开
            print(f"[ui] 读不到 {CONTEST_JS_PATH}：{e}")
            src = ""
        # 内联脚本里不能出现闭合的 script 标签（哪怕是写在注释里）：HTML 解析器见到
        # 它就提前结束脚本，后面整段 JS 变成正文——整页 JS 全废，还很难查。这里兜一下。
        _contest_js_cache = src.replace("</script", "<\\/script")
    return _contest_js_cache


def page(title: str, body: str, nav: str = "", refresh: int = 0,
         math: bool = False, contest_js: bool = True, *, auth: bool = False,
         show_title: bool = True, extra_head: str = "") -> bytes:
    """整页外壳。math=True 时额外引入本地 KaTeX（题面页用，渲染 $...$ 公式）。

    contest_js=True（默认）时把共用前端脚本内联进 `<head>`——放在正文之前，
    页面里的 `initCountdown(...)`（见 `timer_bar`）和 `openModal(...)`（见 `modal`）
    才调得到。整页只有这一份，不额外发请求。
    """
    r = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    math_head = ""
    math_script = ""
    if math:
        math_head = ('<link rel="stylesheet" href="/static/katex/katex.min.css">')
        math_script = r"""
<script defer src="/static/katex/katex.min.js"></script>
<script defer src="/static/katex/auto-render.min.js"></script>
<script>
document.addEventListener("DOMContentLoaded", function () {
  if (!window.renderMathInElement) return;      /* 没加载成功就保持原样（显示 $...$） */
  var boxes = document.querySelectorAll(".stmt");
  if (!boxes.length) boxes = [document.body];
  boxes.forEach(function (box) {
    try {
      renderMathInElement(box, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false }
      ],
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"]
      });
    } catch (e) { /* 渲染失败不影响阅读 */ }
  });
});
</script>"""
    shared = contest_script() if contest_js else ""
    contest_head = f"<script>{shared}</script>" if shared else ""
    return document(title, nav + body, extra_head=r + math_head + contest_head + extra_head,
                    extra_script=math_script, auth=auth, show_title=show_title)


def render_upload_tree(base: str, notes: dict[str, str] | None = None) -> tuple[str, int, int]:
    """把学生上传的目录渲染成树，返回 (树文本, 文件数, 总字节)。

    notes: {相对路径: 备注}，例如 {"GD-0001/candy/candy.cpp": "第 1 题"}。
    老师最想看的就是这个：学生到底传了个什么结构上来（少题、文件夹名写错、
    把 exe 也传上来了等等，一眼就能看出来）。
    """
    entries: list[tuple[str, int]] = []
    for root, dirs, fns in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in fns:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, base).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            entries.append((rel, size))
    if not entries:
        return "", 0, 0
    entries.sort()

    tree: dict = {}
    for rel, size in entries:
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = size

    notes = notes or {}
    junk = tuple(wrapper.JUNK_EXTS)
    lines = []

    def walk(node: dict, prefix: str, path: str) -> None:
        items = sorted(node.items(), key=lambda kv: (isinstance(kv[1], int), str(kv[0])))
        for i, (name, val) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            full = f"{path}/{name}" if path else name
            if isinstance(val, int):
                tags = []
                if notes.get(full):
                    tags.append(f"← {notes[full]}")
                elif name.lower().endswith(junk):
                    tags.append("← 多余文件（编译产物/数据）")
                elif full not in notes and notes:
                    tags.append("← 没用到")
                print_size = _fmt_bytes(val)
                lines.append(f"{prefix}{branch}{name}   ({print_size})"
                             + ("  " + " ".join(tags) if tags else ""))
            else:
                lines.append(f"{prefix}{branch}{name}/")
                walk(val, prefix + ("    " if last else "│   "), full)

    walk(tree, "", "")
    return "\n".join(lines), len(entries), sum(s for _, s in entries)


def rule_badge(contest: dict) -> str:
    rule = store.rule_of(contest)
    cls = {"OI": "tag-oi", "IOI": "tag-ioi", "CSP": "tag-csp"}.get(rule["key"], "")
    return f'<span class="tag {cls}">{html.escape(rule["name"])}</span>'


def level_badge(contest: dict) -> str:
    """级别徽章（J 入门级 / S 提高级）。名字与兜底都在 store 里，页面不另立规则。"""
    level = store.level_of(contest)
    return f'<span class="tag tag-level">{html.escape(store.level_name(level))}</span>'


def rule_tip(contest: dict) -> str:
    """一行赛制提示（学生页面上只用这一行，不再堆长段落）。

    口径：学生看到的名字统一叫「**题目英文名**」（如 candy、m02，老师在本场配题时填的）；
    文件夹名、源文件名、freopen 的文件名三处都用它。学生侧不出现「题目编号」
    （`T00001` 那种 —— 那是老师定位题目用的）。

    成绩可见性三种赛制一致（赛后老师公布），所以提示里只说赛制本身的差异。
    """
    rule = store.rule_of(contest)
    return {
        "OI": "赛中不看分数 · 按最后一次提交计分 · 赛后公布",
        "IOI": "赛中不看分数 · 每题取最高分 · 赛后公布",
        "CSP": "交以考号命名的文件夹 · 代码用 freopen 读写"
               "「题目英文名.in / 题目英文名.out」 · 赛中不看分",
    }.get(rule["key"], "")


#: 考试结束后写在计时条上的那句话（前端到点后替换 .timer-note）
TIMER_OVER_NOTE = "考试已结束，提交通道已关闭（真实考场以监考指令为准）"

#: 计时条后面的启动脚本。放在计时条**后面**，所以能用 previousElementSibling 拿到它，
#: 不用 id（一页里放两条也不会串）。时间戳全在 data-* 上，由服务端下发。
_TIMER_BOOT_JS = """<script>
(function () {
  var bar = document.currentScript.previousElementSibling;
  if (!bar || typeof window.initCountdown !== "function") return;
  window.initCountdown(bar.querySelector(".timer-value"), bar);
})();
</script>"""


def _hms(sec) -> str:
    """秒 → `HH:MM:SS`（小时不封顶，对齐真实系统的「254:08:59」）。"""
    try:
        s = max(0, int(float(sec)))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _duration_text(minutes) -> str:
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


def timer_bar(deadline_ts, duration_min=0, note: str = "",
              label: str = "距离考试结束") -> str:
    """倒计时条（`.timer-bar`）。**截止时刻由服务端下发**，前端只负责走秒。

    deadline_ts: 服务端算出来的截止时刻，Unix 秒（`int(time.time())` 那一套）。
                 给 0/None 表示本场没设截止时刻 → 只显示时长、不倒计。
    duration_min: 比赛时长（分钟），用来生成提示文案，也是没给截止时刻时的兜底显示。
    note: 替代默认提示文案。
    label: 左边那一行的文字。

    为什么坚持服务端下发：原型用 sessionStorage 存「本场结束时刻」，换标签页 /
    重开页面会重新起算，真考试会算错。这里把截止时刻写进 `data-deadline`，
    同时写下 `data-server-now`——学生机时钟可能不准，前端先按这两个值算出与
    服务器的偏移，再只走本地秒差（脚本见 static/contest.js）。
    """
    try:
        end = int(float(deadline_ts))
    except (TypeError, ValueError):
        end = 0
    if note:
        tip = note
    elif duration_min:
        tip = f"考试时长 {_duration_text(duration_min)} · 最后 30 分钟计时条会变红"
    else:
        tip = "最后 30 分钟计时条会变红"

    if end > 0:
        now = int(time.time())
        shown = _hms(end - now)                      # 初始值服务端算好，脚本没跑也显示对
        attrs = (f' data-deadline="{end}" data-server-now="{now}"'
                 f' data-over-note="{html.escape(TIMER_OVER_NOTE, quote=True)}"')
        boot = _TIMER_BOOT_JS
    else:
        shown = _hms(int(duration_min or 0) * 60) if duration_min else "--:--:--"
        attrs = ""
        boot = ""
        tip = note or "本场没有设置截止时刻，以监考指令为准"

    return (f'<div class="timer-bar"{attrs}'
            f'><span class="timer-label">{html.escape(label)}</span>'
            f'<span class="timer-value">{shown}</span>'
            f'<span class="timer-note">{html.escape(tip)}</span></div>{boot}')


def modal(mid: str, title: str, body: str, sub: str = "",
          close_label: str = "关闭（Esc）", head_extra: str = "") -> str:
    """悬浮窗外壳（`.modal-mask` 等类在 CSS 里，开关在 static/contest.js 里）。

    用法：正文里 `modal("view-modal", "题面", body)`，
    再用 `<button type="button" data-modal-open="view-modal">查看题面</button>` 打开。
    点关闭按钮（`[data-modal-close]`）、点遮罩、按 Esc 都会关，页面自己不用写 JS。
    默认带 `hidden`，脚本没跑起来时也不会挡住页面。

    副标题那个 `<span>` **一定带上 id**：`<mid 去掉 -modal>-sub`（`pv-modal` → `pv-sub`、
    `test-modal` → `test-sub`）。页面 JS 打开悬浮窗时要往它写"这道题的编号/英文名/标题"
    （见 admin_pages 的 PROBLEMS_JS、新建题目的 syncHead）。以前这里没给 id，JS 那句
    `document.getElementById('pv-sub').textContent = …` 直接抛 TypeError，**整个点击处理
    函数从第一行就死掉**——表现就是"点『查看题面』/『自己测试』毫无反应"。sub 为空也照样
    输出这个空 span，省得以后又有人踩"元素不存在"。
    """
    sub_id = (mid[: -len("-modal")] if str(mid).endswith("-modal") else str(mid)) + "-sub"
    sub_html = (f' <span class="modal-sub" id="{html.escape(sub_id, quote=True)}">'
                f'{html.escape(sub)}</span>')
    return (f'<div class="modal-mask" id="{html.escape(str(mid), quote=True)}" hidden>'
            f'<div class="modal-panel" role="dialog" aria-modal="true"'
            f' aria-label="{html.escape(title, quote=True)}">'
            f'<div class="modal-head"><h2 class="modal-title">{html.escape(title)}{sub_html}</h2>{head_extra}'
            f'<button type="button" class="modal-close" data-modal-close>'
            f'{html.escape(close_label)}</button></div>'
            f'<div class="modal-body">{body}</div>'
            f'</div></div>')


def sample_link(cid: str, pno, label: str = "大样例") -> str:
    """这道题有大样例就给学生一个下载入口（没有就空着）。"""
    return (f'<a class="btn btn-sm btn-gray" '
            f'href="/sample?c={urllib.parse.quote(cid)}&p={urllib.parse.quote(str(pno))}">'
            f'下载{label}</a>')


def has_samples(pid: str) -> bool:
    if not pid or make_problem is None:
        return False
    return bool(make_problem.load_samples(str(pid)).get("items"))


def student_status_cell(contest: dict, exam: dict, result: dict | None, prob: dict) -> str:
    """学生视角：老师公布成绩之前只给状态，公布之后才给分数。

    **调用方要把好门**：没公布时别调这个函数去显示判定（赛中是零反馈的），
    这里只在"已经公布"的前提下按成绩给分。见 `store.RULES` 上面的说明。
    """
    if not result:
        return '<span class="muted">未提交</span>'
    full = int(prob.get("full", 100))
    got = (result.get("problems") or {}).get(store.problem_dir_name(int(prob["no"])))
    if not got:
        return '<span class="muted">未提交</span>'
    if not exam.get("released"):
        # 不公布分数时只给状态；但"部分正确"这个状态本身不泄露具体分数
        return graded_cell(got, full, show_score=False)
    return graded_cell(got, full) + f' {got.get("score", 0)} 分'
