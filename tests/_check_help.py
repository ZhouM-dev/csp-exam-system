"""本地渲染检查：考生须知（/help）真的能渲染出来，而且内容就是考区通告原文。

**为什么要有这个**：页面正文是一个 f-string，里面的 `{...}` 是 Python 表达式，
写错成字面量（比如通告原文里的 `{路径}`）只有在**真正求值**时才炸 NameError —
`ast.parse` 看不出来（踩过：整页 500，部署到服务器上才发现）。
这里在本地直接调 `_help_page()`，用桩替掉 `_send`，把整页 HTML 渲染一遍。

    python tests/_check_help.py          # 在 work/ 目录下跑

不连服务器、不改数据。
"""

from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
sys.path.insert(0, WORK)

from csp_exam.core import store                       # noqa: E402
from csp_exam.web.student_pages import StudentPages   # noqa: E402
from csp_exam.config import NOTICE_DOC                # noqa: E402

PASS = FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("   [PASS] " + label)
    else:
        FAIL += 1
        print("   [FAIL] " + label)


class Stub(StudentPages):
    """只借真实页面的渲染逻辑：`_nav` 用的是 StudentPages 的那份。"""

    def __init__(self, cid: str, kaohao: str = ""):
        self._cid, self._kaohao = cid, kaohao
        self.status, self.html = None, ""

    # 这三个在真身 web/server.py 里（读 cookie / 写 socket），这里换成桩
    def _session(self):
        return (self._cid, self._kaohao) if self._kaohao else None

    def _student(self, cid: str = ""):
        return self._kaohao or ""

    def _send(self, body, status: int = 200, cookie: str = ""):
        self.status = status
        self.html = body.decode("utf-8") if isinstance(body, bytes) else body

    def _flash(self, kind: str, text: str) -> str:
        return f'<div class="flash flash-{kind}">{text}</div>'


def render(cid: str, kaohao: str = "") -> Stub:
    s = Stub(cid, kaohao)
    s._help_page(cid)          # 炸了就直接 traceback，正好是要看的东西
    return s


def _finish(cid: str, anon, has_notice: bool = True) -> int:
    """与通告在不在无关的那几节（旧版撤没撤、个性化对不对）+ 汇总。"""
    print("=== 3. 旧版「考生须知」（改写版 8 节）确实整段撤了 ===")
    for frag, label in (
        ("八、考试期间能看到什么", "旧版第八节（会看到/不会看到）已撤"),
        ("按测试点给分，过几个给几分", "旧版第五节的「按点给分」口径已撤"),
        ("爆零三件套", "旧版引言已撤"),
    ):
        check(frag not in anon.html, label)

    print("=== 4. 「本场信息」是这一位学生的（通告里只有通用示例）===")
    roster = sorted(store.load_roster(cid).keys())
    if not roster:
        print("   本场没有名单，跳过个性化检查")
    else:
        kh = roster[0]
        mine = render(cid, kh)
        nm = store.student_name(cid, kh)
        check(mine.status == 200, "登录后访问 /help 返回 200")
        check(kh in mine.html, "页面上有这位学生的考号 %s" % kh)
        if nm and nm != "?":
            check(nm in mine.html, "页面上有这位学生的姓名 %s" % nm)
        check("个人信息文件" in mine.html, "「本场信息」里写了个人信息文件")
        check("本场应提交" in mine.html, "写了本场应交的文件名")
        tree_ok = all(store.code_of(p) in mine.html for p in store.load_exam(cid).get("problems", []))
        check(tree_ok, "目录树按本场每题的英文名生成")
        if has_notice:
            check("GD-S00001" in mine.html, "通告里的通用示例（GD-S00001）原样保留")
        # 未登录时也不能出现别的学生的考号
        others = [k for k in roster if k != kh]
        check(not any(k in anon.html for k in others),
              "未登录状态下不泄露任何学生的考号")

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


def main() -> int:
    cid = "c1"
    contest = store.get_contest(cid)
    if not contest:
        print("本地没有 %s 的数据，跑不了（先跑一遍端到端或 reset_exam.sh）" % cid)
        return 2

    print("=== 1. 通告原文渲染（未登录也要能看）===")
    anon = render(cid)
    check(anon.status == 200, "未登录访问 /help 返回 200（status=%s）" % anon.status)
    check("考生须知" in anon.html, "页面标题是「考生须知」")
    # 通告是考区的东西，**公开仓库里不带**（版权声明见文件末尾，build_publish.py 会排除），
    # 所以这里两种情形都算正常：有文件就查原文，没文件就查那条兜底提示。
    has_notice = os.path.isfile(NOTICE_DOC)
    if not has_notice:
        print("   （本机没有 %s，按「公开仓库」情形查兜底提示）" % os.path.basename(NOTICE_DOC))
        check("以考点下发的纸质通告为准" in anon.html, "通告文件缺失时给兜底提示，不 500")
        check("noi.cn" in anon.html, "兜底提示里带着官网链接")
        check("<h2>附：本场信息</h2>" in anon.html, "通告没有也照样给「本场信息」")
        return _finish(cid, anon, has_notice=False)
    check("广东省认证考生注意事项" in anon.html, "通告标题出现在页面上")
    # 抽几条通告里的关键句（挑不同位置的，防止只渲染出前半页）
    for frag, label in (
        ("严格遵从监考教师的指引完成认证机考", "第 1 条在"),
        ("以自己的考号命名的目录", "第 2 条（考号目录）在"),
        ("大小写字母的区别", "第 3 条（命名红线）在"),
        ("以自己名字为文件名的文本文件", "第 4 条（个人信息文件）在"),
        ("task3.cpp", "第 5 条的目录结构图在"),
        ("不带任何绝对路径", "第 6 条（文件读写）在"),
        ("无法通过编译", "第 7 条（NOI Linux 编译坑）在"),
        ("全文比较方式", "第 8 条（输出比较）在"),
        ("不能带任何资料进场", "第 9 条（考场纪律）在"),
        ("诚信考试及知情同意书", "第 10 条（知情同意书）在"),
        ("每15分钟保存一次程序", "第 11 条（每 15 分钟保存）在"),
        ("祝各位选手高水平发挥", "第 12 条（末条）在"),
        ("未经NOI竞赛办公室书面授权", "通告自带的版权声明也在"),
        # 「位随机数」是别的页面（管理端考号表、_e2e_roster.sh）都在用的口径写法，
        # 这里跟着写「5 位随机数」；别写成「5 位纯随机数」——搜不到了（踩过）
        ("位随机数", "「本场信息」的发号口径与别处一致（5 位随机数）"),
    ):
        check(frag in anon.html, label)
    check("notice_guangdong.md</code> 缺失" not in anon.html,
          "通告文件在（没走「文件缺失」那条兜底）")

    print("=== 2. 渲染成 HTML 了，不是原样倒 Markdown ===")
    # 只看通告那一块：整页里还有内联的 CSS/JS，注释里本来就写着 **（用来强调），
    # 拿整页判断会误报。
    z0 = anon.html.find('<div class="card stmt"')
    z1 = anon.html.find("<h2>附：本场信息</h2>")
    check(z0 > 0 and z1 > z0, "通告那个 <div class=\"card stmt\"> 在（%d..%d）" % (z0, z1))
    notice_zone = anon.html[z0:z1]
    check("<h2>" in notice_zone, "标题渲染成了 <h2>（不是裸的 # 号）")
    check("**" not in notice_zone, "通告里没有剩下的 ** 星号")
    check("`__int64`" not in notice_zone and "<code>__int64</code>" in notice_zone,
          "行内代码渲染成了 <code>")

    return _finish(cid, anon, has_notice=True)


if __name__ == "__main__":
    sys.exit(main())
