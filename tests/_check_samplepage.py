"""本地渲染检查：学生端「大样例」页 —— 点开样例能看到和管理端同款的对比布局。

要盯的三件事：
  1. 输入/答案**直接渲染出来**（不是只有下载链接）；
  2. 布局与老师端「测试点详情」同款（`输入数据` / `标准答案` 标题 + `<pre class="code-view">`）；
  3. 大文件只显示前 15 行并说明，二进制/读不了的不硬渲染（只留下载）。

    python tests/_check_samplepage.py      # 在 work/ 目录下跑

样例数据是现造的（打桩掉 load_samples / sample_path），不碰 data/ 里的真实大样例。
"""

from __future__ import annotations

import os
import re
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


CID, KH = "ZZ-SAMPLE", "GD-S00011"
CONTEST = {"id": CID, "title": "样例检查用", "rule": "CSP", "level": "S"}
EXAM = {"problems": [{"no": 1, "pid": "P-SAMPLE", "full": 100, "name": "candy",
                      "title": "糖果"}]}
TMP = tempfile.mkdtemp(prefix="sample-")
BIG_IN = "".join(f"{i} {i + 1}\n" for i in range(1, 41))      # 40 行：会被截到 15 行


def _write(name: str, text, binary: bool = False) -> None:
    path = os.path.join(TMP, name)
    if binary:
        with open(path, "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


_write("大样例.in", BIG_IN)
_write("大样例.out", "3\n5\n7\n")
_write("附件.bin", b"\x00\x01\x02a", binary=True)
SAMPLES = {"items": [
    {"kind": "big", "label": "第 1 组", "files": [
        {"name": "大样例.in", "role": "in", "size": len(BIG_IN)},
        {"name": "大样例.out", "role": "out", "size": 6},
        {"name": "附件.bin", "role": "other", "size": 4},
    ]},
]}


class Stub(SP.StudentPages):
    def __init__(self):
        self.status, self.html = None, ""

    def _send(self, body, status: int = 200, cookie: str = ""):
        self.status = status
        self.html = body.decode("utf-8") if isinstance(body, bytes) else body

    def _redirect(self, url, cookie: str = ""):
        self.status, self.html = 302, url

    def _flash(self, kind: str, text: str) -> str:
        return f'<div class="flash flash-{kind}">{text}</div>'


def main() -> int:
    real = (store.load_exam, store.code_of, store.student_name, store.rule_of,
            store.level_of, store.duration_of, store.load_roster)
    mk = SP.make_problem
    real_mk = (mk.load_samples, mk.sample_path)
    store.load_exam = lambda cid: EXAM
    mk.load_samples = lambda pid, base_dir="": SAMPLES
    mk.sample_path = lambda pid, name, base_dir="": (
        os.path.join(TMP, name) if os.path.isfile(os.path.join(TMP, name)) else None)
    try:
        s = Stub()
        s._sample_page(KH, CONTEST, CID, "1")
        # 先把内联的 <script> 剔掉再断言：共享脚本的注释里也写着 `<pre class="code-view">`
        # 这种字样，拿整页数标记会数多（踩过两次了，这类断言一律先剥脚本）
        h = re.sub(r"<script[\s\S]*?</script>", "", s.html)
        check(s.status == 200, "页面 200")
        check("<details" in h and "点开看输入与答案" in h, "每个样例组默认收起，提示「点开看…」")
        check("输入数据" in h and "标准答案" in h,
              "同款标题：输入数据 / 标准答案 都在（老师端测试点详情就是这么写的）")
        check(h.count('<pre class="code-view">') == 2, "两块 <pre class=code-view>（输入 + 答案）")
        check("1 2" in h and "3\n" in h, "输入与答案的内容真的渲染出来了")
        check("只显示前 15 行" in h, "40 行的输入被截到 15 行并说明了")
        check("附件.bin" in h and "下载" in h, "附件仍然只给下载（不硬渲染）")
        check("x00" not in h.replace("\\x00", ""), "二进制内容没有被塞进页面")
    finally:
        (store.load_exam, store.code_of, store.student_name, store.rule_of,
         store.level_of, store.duration_of, store.load_roster) = real
        (mk.load_samples, mk.sample_path) = real_mk
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
