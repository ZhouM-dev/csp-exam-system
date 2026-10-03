"""本地渲染检查：学生「查成绩」页 —— 公布成绩**之后**能看到逐点情况与自己交的代码。

三条底线：
  1. 没公布 → 一个字都不给（零反馈）；
  2. 公布了 + 查自己 → 逐点明细 + 自己的代码；
  3. 公布了 + 查**别人** → 只给分数表，**不给代码、不给逐点明细**（别人的东西不能互相看）。

    python tests/_check_scorepage.py        # 在 work/ 目录下跑（本机，不连服务器）

数据全是现造的（打桩掉 store 的读盘函数），不碰 data/ 里的真实成绩。
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
sys.path.insert(0, WORK)

import csp_exam.web.student_pages as SP                        # noqa: E402
from csp_exam.core import store                                # noqa: E402

PASS = FAIL = 0


def check(ok: bool, label: str):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("   [PASS] " + label)
    else:
        FAIL += 1
        print("   [FAIL] " + label)


CID = "ZZ-CHECK"
ME = "GD-S00007"
OTHER = "GD-S00009"
SRC_ME = ('#include <cstdio>\nint main(){freopen("candy.in","r",stdin);\n'
          '  freopen("candy.out","w",stdout);int a,b;scanf("%d %d",&a,&b);\n'
          '  printf("%d\\n",a+b);return 0;}\n')
CONTEST = {"id": CID, "title": "本地检查用比赛", "rule": "CSP", "level": "S",
           "prefix": "GD", "duration_min": 240}
PROB = {"no": 1, "pid": "P-CHECK", "full": 100, "name": "candy", "title": "糖果分配"}


def exam(released: bool) -> dict:
    return {"released": released, "problems": [dict(PROB)]}


def results() -> dict:
    """一份"交了两题、第一题部分对"的成绩记录（含逐点明细）。"""
    cases = [
        {"no": "1", "status": 1, "status_text": "答案正确", "score": 50,
         "time": 3, "memory": 1024, "input": "1 2", "output": "3", "answer": "3"},
        {"no": "2", "status": 2, "status_text": "答案错误", "score": 0,
         "time": 4, "memory": 1030, "input": "5 7", "output": "11", "answer": "12",
         "message": "第 1 行不一致：期望 12，得到 11"},
    ]
    return {
        ME: {"problems": {"T1": {"score": 50, "status": 2, "status_text": "部分正确",
                                 "file": f"{ME}/candy/candy.cpp", "tries": 1,
                                 "testcases": cases, "time": 4, "memory": 1030}},
             "submitted_at": "2026-10-03 21:00:00", "total": 50},
        OTHER: {"problems": {"T1": {"score": 100, "status": 1, "status_text": "答案正确",
                                    "file": f"{OTHER}/candy/candy.cpp", "tries": 1,
                                    "testcases": [], "time": 2, "memory": 1000}},
                "submitted_at": "2026-10-03 21:05:00", "total": 100},
    }


ROSTER = {ME: {"name": "甲同学"}, OTHER: {"name": "乙同学"}}


class Stub(SP.StudentPages):
    """只借渲染逻辑：存储层换成上面那份现造数据。"""

    def __init__(self, session_kh: str = ""):
        self.session_kh = session_kh
        self.status, self.html = None, ""

    def _send(self, body, status: int = 200, cookie: str = ""):
        self.status = status
        self.html = body.decode("utf-8") if isinstance(body, bytes) else body

    def _redirect(self, url, cookie: str = ""):
        self.status, self.html = 302, url

    def _flash(self, kind: str, text: str) -> str:
        return f'<div class="flash flash-{kind}">{text}</div>'

    def _session(self):
        return (CID, self.session_kh) if self.session_kh else None

    def _student(self, cid: str = ""):
        return self.session_kh or ""


def render(session_kh: str, kaohao_input: str = "") -> Stub:
    s = Stub(session_kh)
    s._score_query_page(kaohao_input, CONTEST, CID)
    return s


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="scorepage-")
    # 造一份"学生交的文件"，让「你交的代码」那一块有东西可读
    d = os.path.join(tmp, ME, "candy")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "candy.cpp"), "w", encoding="utf-8") as f:
        f.write(SRC_ME)

    real = (store.load_exam, store.load_results, store.load_roster, store.upload_dir,
            store.student_name, store.entry_total)
    store.load_exam = lambda cid: exam(CUR["released"])
    store.load_results = lambda cid: results()
    store.load_roster = lambda cid: ROSTER
    store.upload_dir = lambda cid, kh: os.path.join(tmp, kh)
    store.student_name = lambda cid, kh: (ROSTER.get(kh) or {}).get("name", "?")
    store.entry_total = lambda e: int(e.get("total") or 0)
    try:
        print("=== 1. 没公布成绩：一个字都不给（零反馈）===")
        CUR["released"] = False
        p = render(ME)
        check(p.status == 200 and "成绩还没有公布" in p.html, "只给「成绩还没有公布」")
        check("<h2>逐题明细</h2>" not in p.html and "<b>你交的代码</b>" not in p.html,
              "没有明细、没有代码（查的是 h2/加粗标记，别被内联 JS 里的注释骗了）")

        print("=== 2. 已公布 + 看自己：逐点情况 + 自己交的代码 ===")
        CUR["released"] = True
        p = render(ME)
        check(p.status == 200, "页面 200")
        check("逐题明细" in p.html, "有「逐题明细」一节")
        check("1 / 2 个测试点通过" in p.html, "写清了过几个点（1 / 2）")
        check("答案错误" in p.html and "第 1 行不一致" in p.html,
              "逐点里能看到没过的那个点，以及**说明**（为什么错）")
        check("50</b> / 100 分" in p.html or ">50<" in p.html, "这一题的分与满分都在")
        check("你交的代码" in p.html and "freopen" in p.html,
              "能看到**自己交的代码**（原文里带了 freopen）")
        check("candy.cpp" in p.html, "标了是哪一份文件")
        check("学生程序的输出评测机不保存" in p.html, "说明了「没有你的输出」这件事，不让人误会")
        # 复制按钮：代码块的**正文**里不能有行号（行号是另起一列），
        # 否则「复制代码」粘出来会带 `  1  ` 这种前缀（踩过）
        body = p.html.split('class="code-view code-numbered code-body">', 1)
        check(len(body) == 2, "代码是独立的正文块（行号另起一列）")
        if len(body) == 2:
            first = body[1].split("\n", 1)[0]
            check(not first.lstrip().startswith("1  "), "正文第一行没有被塞进行号：%r" % first[:40])
        check("cspAddCopyButtons" in p.html, "页面里带了「复制代码」按钮的脚本（共享 JS 内联）")

        print("=== 3. 已公布 + 查别人：只给分数，不给代码/明细 ===")
        p = render(ME, OTHER)
        check(p.status == 200 and "乙同学" in p.html, "能看到对方的总分表（分数本来就是公开的）")
        check("<b>你交的代码</b>" not in p.html, "**看不到对方的代码**")
        check("<h2>逐题明细</h2>" not in p.html, "**看不到对方的逐点明细**")

        print("=== 4. 登录着直接进查成绩：不用再手输考号 ===")
        p = render(ME)
        check(f'value="{ME}"' in p.html, "考号框预填了自己的考号")
        check("甲同学" in p.html, "直接就是自己的成绩")
    finally:
        (store.load_exam, store.load_results, store.load_roster, store.upload_dir,
         store.student_name, store.entry_total) = real
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


CUR = {"released": True}

if __name__ == "__main__":
    sys.exit(main())
