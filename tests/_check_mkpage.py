"""本地渲染检查：管理端「新建题目」页 + 题目删除/重名那套逻辑。

**为什么要有这个**：这一页的 HTML 是一整个 f-string，里面的 JS/CSS 花括号要写两遍
（`{{` `}}`）。写漏一个，Python 就把它当成表达式去求值 —— `ast.parse` 看不出来，
只有**真正渲染**才炸（踩过：整页 500）。这里在本地把页面渲染出来，逐项核对。

    python tests/_check_mkpage.py          # 在 work/ 目录下跑

不连服务器、不连评测站、不改数据。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
sys.path.insert(0, WORK)

from csp_exam.core import problems as P                        # noqa: E402
from csp_exam.web.admin_pages import AdminPages                # noqa: E402

PASS = FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("   [PASS] " + label)
    else:
        FAIL += 1
        print("   [FAIL] " + label)


class Stub(AdminPages):
    """只借真实页面的渲染逻辑（`_admin_nav` 等），认证与输出换成桩。"""

    def __init__(self):
        self.status, self.html = None, ""

    def _check_admin(self, key: str) -> bool:
        return True

    def _send(self, body, status: int = 200, cookie: str = ""):
        self.status = status
        self.html = body.decode("utf-8") if isinstance(body, bytes) else body

    def _redirect(self, url, cookie: str = ""):
        self.status, self.html = 302, url

    def _flash(self, kind: str, text: str) -> str:
        return f'<div class="flash flash-{kind}">{text}</div>'

    def _admin_login(self, error: str = ""):
        self.status, self.html = 401, error


def render_mkpage() -> Stub:
    s = Stub()
    s._admin_problem({"key": "test-key"})
    return s


def main() -> int:
    print("=== 1. 「新建题目」页能渲染出来（f-string 里没写错的字面量花括号）===")
    p = render_mkpage()
    check(p.status == 200, "渲染返回 200（status=%s）" % p.status)
    check("<h2>新建题目</h2>" in p.html, "页面标题在")
    # 花括号写错时常见的两种残留（{{ 或 }} 会原样出现在 HTML 里）
    check("{{" not in p.html and "}}" not in p.html, "页面里没有残留的双花括号")
    # **真正证明**花括号写对了：源码里的 `{{` 渲染出来必须正好是单个 `{`。
    # （写漏一个的话，Python 会把那段当表达式求值 —— 要么 NameError 整页 500，
    #   要么渲染出一个莫名其妙的值；两种都会在这里露出来。）
    check(".up-bar.busy > i {" in p.html, "CSS 的 `{` 渲染成了单个左花括号")
    check("@keyframes csp-up { from { background-position: 0 0; }" in p.html,
          "keyframes 的嵌套花括号也对（from {...} 在）")
    check("form.addEventListener('submit', function (ev) {" in p.html,
          "JS 的 `{` 也是单个（没被当成表达式）")

    print("=== 2. 表单本体还在（改版没把老功能碰掉）===")
    for frag, label in (
        ('id="mk-form"', "表单在"),
        ('name="folder"', "① 选文件夹在"),
        ('name="title"', "② 标题在"),
        ('name="name"', "英文名（默认）在"),
        ('name="time_ms"', "时限在"),
        ('name="memory_mb"', "内存在"),
        ('id="stmt-src"', "③ 题面框在"),
        ('id="std-src"', "④ 标程框在"),
        ('name="bigsample"', "⑤ 大样例在"),
        ('id="mk-detect"', "本机识别结果的落点在"),
    ):
        check(frag in p.html, label)

    print("=== 3. 上传进度那套在（本次新增）===")
    for frag, label in (
        ('id="mk-up"', "进度面板在"),
        ('id="mk-up-bar"', "进度条的槽在"),
        ('id="mk-up-fill"', "进度条的填充在"),
        ('id="mk-up-title"', "进度标题在"),
        ('id="mk-up-pct"', "百分比在"),
        ('id="mk-up-note"', "进度说明在"),
        (".up-bar.busy", "「服务端处理中」的流动条纹样式在"),
        ("@keyframes csp-up", "条纹动画的 keyframes 在（花括号没写坏）"),
        ("xhr.upload.onprogress", "用 XHR 报传输百分比"),
        ("xhr.upload.onload", "传完之后切到「服务端正在建题」"),
        ("new FormData(form)", "整个表单原样上传（字段名/form 结构不变）"),
        ("location.href = target", "跑完跟着服务端的 302 走，提示仍由服务端渲染"),
        ("target !== form.action", "判断有没有跳转时比的是绝对地址 form.action（不是相对地址）"),
        ("document.write(xhr.responseText)",
         "没跳转（服务端直接回显失败页）时把那份 HTML 装进来，红框提示不会丢"),
    ):
        check(frag in p.html, label)

    print("=== 3b. 选完文件要能「松手」（Windows 上不松手 = 文件被占用、改不了）===")
    for frag, label in (
        ('id="mk-clear"', "有「清空已选文件」按钮"),
        ("function releasePicks()", "有松手函数"),
        ("el.value = ''", "松手 = 把文件输入框清空（浏览器才会放掉那些文件）"),
        ("文件传完了就松手", "文件一传完就自动松手（服务端建题那十几秒里老师就能改文件了）"),
        ("clearBtn.addEventListener('click', function () { releasePicks(); })",
         "按钮点了就松手"),
        ("不再占着这些文件", "松手后给了明确的提示（要重选一次）"),
        ("选完文件夹之后，浏览器会", "选文件的地方写清了「浏览器会一直占着」这件事"),
    ):
        check(frag in p.html, label)
    check('"folder", "data", "std", "bigsample"' in p.html or
          "'folder', 'data', 'std', 'bigsample'" in p.html,
          "松手时会清掉全部四个文件输入框（文件夹/zip/标程/大样例）")
    check(p.html.count("releasePicks()") >= 3,
          "松手函数不只定义、还真的被调了（定义 1 次 + 自动/按钮各 1 次，实际 %d 次）"
          % p.html.count("releasePicks()"))
    for frag, label in (
    ):
        check(frag in p.html, label)
    check("请别关页面" in p.html, "明确告诉老师服务端那段别关页面")
    check("一段没有细粒度进度" in p.html or "没有细粒度进度" in p.html,
          "不吹牛：服务端那段没有百分比就说没有")

    print("=== 4. 重名/删除那套逻辑（纯逻辑，不连评测站）===")
    check(P.free_pid("G01", set()) == "G01", "标识没被占：原样用")
    check(P.free_pid("G01", {"G01"}) == "G01b", "被占了：让到 G01b")
    check(P.free_pid("G01", {"G01", "G01b", "G01c"}) == "G01d", "接着往后让")
    long_pid = "x" * 40
    got = P.free_pid(long_pid, {long_pid})
    check(len(got) <= 40 and got.endswith("b"), "40 字顶格时截一下再加后缀（长度 %d）" % len(got))
    check(P.free_pid("", set()) == "problem", "空标识兜个底，不炸")
    # 让位标识必须符合评测站的 pid 规矩（只认字母开头的字母数字），
    # 否则它会悄悄把题建成 #N —— 老师搜索不到，站点上多一道僵尸题（踩过）
    gots = [P.free_pid(b, {b}) for b in ("G01", "candy2", "P1000", "Z")]
    check(all(g.isalnum() and g[0].isalpha() for g in gots),
          "让位标识都是「字母开头的字母数字」：%s" % "、".join(gots))

    print("=== 5. 「已删除」记号：core 里读写的是同一份登记 ===")
    src = open(os.path.join(WORK, "csp_exam", "web", "admin_pages.py"), encoding="utf-8").read()
    check("load_problem_info = make_problem.load_problem_info" in src,
          "页面层不再自己实现一遍登记读写（同一份实现）")
    check("make_problem.set_deleted(pid, flag)" in src, "页面的打记号走 core")
    imp = open(os.path.join(WORK, "csp_exam", "core", "importer.py"), encoding="utf-8").read()
    check("_mk.is_deleted(p)" in imp, "题单导入也认这个记号（假删除的不算占用）")
    check("return _problem_doc_id(pid) == \"\"" in imp.replace("'", "\""),
          "删题后**复查**：查不到了才算删掉")
    check("failed.append(\"评测站题目\")" in src, "彻底删除删不掉时报错，不报假成功")

    print("=== 6. 「题目列表」页也还渲染得出来（删除/恢复/彻底删除都在这页上）===")
    s = Stub()
    try:
        s._admin_problems({"key": "test-key"})
        check(s.status == 200, "题目列表渲染 200（status=%s）" % s.status)
        check("题目列表" in s.html, "页面标题在")
        check("action\" value=\"delete\"" in s.html or "删除" in s.html, "有删除入口")
    except Exception as e:                                   # noqa: BLE001
        check(False, "题目列表渲染炸了：%r" % (e,))

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
