#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地题库与 HydroJudge 入口。编译、运行、检查答案和逐点判定交给原版 HydroJudge。

平台负责数据校验与结果格式转换，不维护另一套判题器，也不自动回退。
file 模式使用题目指定的 .in/.out 文件；stdin 模式使用标准输入输出。
"""

from __future__ import annotations

import hashlib
import os
import re
import time

from . import gojudge
from .util import log
from ..config import DATA_DIR

#: 题目数据目录（本地题目库）
PROBLEMS_DIR = os.path.join(DATA_DIR, "problems")

ST_AC, ST_WA, ST_TLE, ST_MLE, ST_OLE, ST_RE, ST_CE, ST_SE = 1, 2, 3, 4, 5, 6, 7, 8
LANGS = {'.cpp', '.cc', '.cxx', '.c++', '.c'}


# ------------------------------------------------------------------ 题目数据

def valid_pid(pid: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", str(pid))) and str(pid) not in (".", "..")


def cases_dir(pid: str) -> str:
    if not valid_pid(pid):
        raise ValueError("题目标识无效")
    root = os.path.realpath(PROBLEMS_DIR)
    path = os.path.realpath(os.path.join(root, str(pid), "testdata"))
    if os.path.commonpath([root, path]) != root:
        raise ValueError("题目数据路径越界")
    return path


def _data_read(fn):
    from functools import wraps
    @wraps(fn)
    def locked(*args, **kwargs):
        from . import store
        with store._LOCK:
            return fn(*args, **kwargs)
    return locked


@_data_read
def cases_of(pid: str) -> list[dict]:
    """这道题的测试点：`[{"name","in","out"}]`，按名字自然排序（2 排在 10 前）。

    成对找 `.in` + 同名 `.out`；只有输入没有答案的点也收下（out=None），
    这种点在旧系统里是"对任何输出都判错"，这里保持一致（调用方会提示老师）。
    """
    d = cases_dir(pid)
    if not os.path.isdir(d):
        return []
    names = sorted({os.path.splitext(f)[0] for f in os.listdir(d)
                    if f.endswith(".in")}, key=_natural)
    out = []
    for n in names:
        ip = os.path.join(d, n + ".in")
        op = os.path.join(d, n + ".out")
        try:
            with open(ip, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        want = None
        if os.path.isfile(op):
            try:
                with open(op, "rb") as f:
                    want = f.read()
            except OSError:
                want = None
        out.append({"name": n, "in": raw, "out": want})
    return out


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def limits_of(pid: str) -> tuple[int, int]:
    """这道题的 (时限毫秒, 内存 MB)。

    建题时记在 `data/problem_info.json`（`problem_info.json` 的 `time_ms`/`memory_mb`）；
    缺少或非法配置时停止评测，不能猜测题目的官方限额。
    """
    from . import problems as make_problem
    rec = make_problem.load_problem_info().get(str(pid)) or {}
    try:
        t, m = int(rec.get("time_ms") or 0), int(rec.get("memory_mb") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError(f"题目 {pid} 的时限或内存配置无效") from e
    if t <= 0 or m <= 0:
        raise ValueError(f"题目 {pid} 缺少时限或内存，请按官方题面补齐后重测")
    return t, m


def known_pids() -> set:
    """本地题目库里有哪些题（`data/problems/` 下有目录的）+ 登记表里的。

    「标识被占用」这件事从此看这里，而不是问评测站 —— 换判题器之后本地就是权威。
    """
    out = set()
    if os.path.isdir(PROBLEMS_DIR):
        for name in os.listdir(PROBLEMS_DIR):
            if os.path.isdir(os.path.join(PROBLEMS_DIR, name)):
                out.add(name)
    try:
        from . import problems as make_problem
        out |= set(make_problem.load_codes()) | set(make_problem.load_problem_info())
    except Exception:                                         # noqa: BLE001
        pass
    return out


def store_problem(pid: str, cases: list[dict], statement: str = "") -> dict:
    """把一份题目数据**存进本地题目库**（建题/题单导入都走这里，不再推给评测站）。

    `cases` 就是 `create_problem` 里配好的那批：`[{"name","in","out"}]`（`in`/`out` 是 bytes）。
    返回 `{"cases": n, "bytes": n, "dir": …}`。

    `in`/`out` **必须是 bytes**：以前这里对 str 做了个 `str(ib).encode()` 的"兜底"，
    结果把上游漏掉的一步（路径→内容）悄悄写成了路径字符串，题目建出来页面上一切正常、
    学生交什么都判错（踩过：老师新建的几道题全中招）。现在直接报错，不再咽下去。
    """
    import shutil
    pid = str(pid or "").strip()
    d = cases_dir(pid)
    for i, c in enumerate(cases, 1):
        if c.get("out") is None:
            raise OSError(f"测试点 {c.get('name') or i} 缺少标准答案，不能建题")
        for k in ("in", "out"):
            v = c.get(k)
            if v is not None and not isinstance(v, bytes):
                raise OSError(f"测试点 {c.get('name') or i} 的 {k} 不是 bytes"
                              f"（拿到 {type(v).__name__}）—— 上层忘了把路径换成内容？")
    import tempfile
    from . import store
    for i, c in enumerate(cases, 1):
        name = str(c.get("name") or i)
        if name in (".", "..") or "/" in name or "\\" in name:
            raise OSError("测试点文件名无效")
    os.makedirs(os.path.dirname(d), exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".testdata-", dir=os.path.dirname(d))
    total = 0
    try:
        for i, c in enumerate(cases, 1):
            name = str(c.get("name") or i)
            for suffix, raw in (("in", c.get("in") or b""), ("out", c.get("out") or b"")):
                with open(os.path.join(stage, f"{name}.{suffix}"), "wb") as f:
                    f.write(raw)
                total += len(raw)
        with store._LOCK:
            previous = d + ".previous"
            if os.path.exists(previous):
                raise OSError("题目数据仍有未完成的更新，请先检查")
            if os.path.exists(d):
                os.rename(d, previous)
            try:
                os.rename(stage, d)
            except OSError:
                if os.path.exists(previous): os.rename(previous, d)
                raise
            shutil.rmtree(previous, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if statement and statement.strip():
        from . import store as _store
        from . import database
        database.save_statement(_store.DATA_DIR, pid, statement)
    return {"cases": len(cases), "bytes": total, "dir": d}


def drop_problem_data(pid: str) -> list:
    """彻底删掉一道题的本地数据（测试点目录 + 题面）。返回清掉了什么。"""
    import shutil
    gone = []
    d = os.path.dirname(cases_dir(pid))
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        gone.append("测试点")
    from . import store as _store
    from . import database
    if database.delete_statement(_store.DATA_DIR, pid):
        gone.append("题面")
    return gone


def problem_ready(pid: str) -> tuple[bool, str]:
    """这道题能不能在本机判：数据在不在。返回 (能不能, 一句说明)。"""
    cs = cases_of(pid)
    if not cs:
        return False, (f"本机没有题目 {pid} 的测试数据（{cases_dir(pid)}）——"
                       f"先从评测站迁一次：python3 tests/_migrate_from_hydro.py")
    no_ans = [c["name"] for c in cs if c["out"] is None]
    if no_ans:
        return False, (f"{len(cs)} 个测试点，其中 {len(no_ans)} 个没有答案"
                       f"（{'、'.join(no_ans[:5])}）：请补齐后评测")
    return True, f"{len(cs)} 个测试点，数据齐"


# ------------------------------------------------------------------ 判一道题

def judge_source(pid: str, source: str, ext: str, *, code: str, full: int,
                 time_ms: int = 1000, memory_mb: int = 256,
                 io_mode: str = "file", profile=None) -> dict:
    from ..config import JUDGE_SLOTS
    with JUDGE_SLOTS:
        return _judge_source_locked(pid, source, ext, code=code, full=full,
                                    time_ms=time_ms, memory_mb=memory_mb, io_mode=io_mode, profile=profile)


def _judge_source_locked(pid, source, ext, *, code, full, time_ms, memory_mb, io_mode, profile):
    """判一份源码，返回**与旧 Hydro 记录同形状**的 row。

    row 里这些字段是上游在用的（`grading._apply_result` / `_norm_case`）：
        status          整数状态码（1 通过 / 2 答案错误 / 3 超时 / 4 内存 / 6 运行错误 / 7 编译错误 / 8 系统错误）
        testcases       逐点明细（每点带 score —— 按点给分靠它，缺了总分就是 0）
        time / memory   测试点 CPU 总时间毫秒 / 峰值 KB（与 Hydro 口径一致：毫秒、KB）
        compilerTexts   编译报错原文（管理端「编译错误时直接给报错」）
        note            不该算分的失败（编译不过、本机没数据…）——有它就不计提交次数
    """
    code = str(code or "")
    ext = str(ext or "").lower()
    from ..config import MAX_SOURCE_BYTES
    if len(source.encode("utf-8") if isinstance(source, str) else source) > MAX_SOURCE_BYTES:
        return {"status": ST_CE, "score": 0, "note": "源文件超过官方 100 KiB 上限", "testcases": []}
    if io_mode == "file":
        from .wrapper import csp_source_violation
        violation = csp_source_violation(source)
        if violation:
            return {"status": ST_CE, "score": 0, "note": violation, "testcases": []}
    cases = cases_of(pid)
    if not cases:
        return {"status": ST_SE, "score": 0, "note": f"本机没有题目 {pid} 的测试数据",
                "testcases": []}
    if any(c["out"] is None for c in cases):
        return {"status": ST_SE, "score": 0, "note": "测试数据缺少标准答案，需老师补齐后重测",
                "testcases": []}
    if ext not in LANGS:
        return {"status": ST_CE, "score": 0,
                "note": f"暂不支持 {ext or '这种'} 语言（当前 HydroJudge 配置支持 C/C++）",
                "testcases": []}

    # 每个测试点的满分：满分 ÷ 点数，余数摊给前面几个点（与 grading._point_scores 一致）
    from .grading import _point_scores     # 延迟导入：避免和 grading 循环 import
    per = _point_scores(full, len(cases))

    try:
        from . import judge_profile
        environment = gojudge.ensure_ready()
        effective_ms, profile = judge_profile.effective_limit(time_ms, profile=profile)
        if int(memory_mb) <= 0:
            raise ValueError("题目内存限制必须大于零")
        from . import hydro_client
        row = hydro_client.judge(pid, source, ext, cases, per, code=code, time_ms=effective_ms,
                           memory_mb=memory_mb, io_mode=io_mode)
        row["environment"] = environment
        row["official_time_ms"] = int(time_ms)
        row["effective_time_ms"] = effective_ms
        row["time_scale"] = profile["time_scale"]
        row["calibrated"] = profile["calibrated"]
        row["reference_time"] = round((row.get("time") or 0) / profile["time_scale"], 3)
    except (gojudge.GoJudgeError, ValueError) as e:
        # 沙箱挂了：算「系统错误」，不计提交次数（和评测机返回 SE 时一样）
        return {"status": ST_SE, "score": 0, "note": f"判题沙箱不可用：{e}",
                "testcases": []}
    return row


def selftest() -> dict:
    """自检：引擎本身对不对（不需要题目数据，现造两个点）。

    要盖住：AC、WA、TLE、编译错误，以及** freopen 与不用 freopen 两种写法在
    stdin/file 两种口径下的差别**——这正是老师反馈"结果不一样"的地方。
    """
    ok_all = True
    checks = []
    import uuid
    pid = "_selftest_" + uuid.uuid4().hex[:12]
    d = cases_dir(pid)
    os.makedirs(d, exist_ok=True)
    try:
        with open(os.path.join(d, "1.in"), "wb") as f:
            f.write(b"1 2\n")
        with open(os.path.join(d, "1.out"), "wb") as f:
            f.write(b"3\n")
        with open(os.path.join(d, "2.in"), "wb") as f:
            f.write(b"5 7\n")
        with open(os.path.join(d, "2.out"), "wb") as f:
            f.write(b"12\n")

        AC_STDIN = ('#include <cstdio>\nint main(){int a,b;if(scanf("%d %d",&a,&b)!=2)'
                    'return 1;printf("%d\\n",a+b);return 0;}\n')
        AC_FREOPEN = ('#include <cstdio>\nint main(){freopen("candy.in","r",stdin);'
                      'freopen("candy.out","w",stdout);int a,b;scanf("%d %d",&a,&b);'
                      'printf("%d\\n",a+b);return 0;}\n')
        WRONG = ('#include <cstdio>\nint main(){int a,b;scanf("%d %d",&a,&b);'
                 'printf("%d\\n",a*b);return 0;}\n')
        TLE = ('#include <cstdio>\nint main(){freopen("candy.out","w",stdout);for(;;);return 0;}\n')
        CE = 'int main(){this is not c++}\n'

        def one(src, label, **kw):
            nonlocal ok_all
            row = judge_source(pid, src, ".cpp", code="candy", full=100,
                               time_ms=1000, memory_mb=256, **kw)
            checks.append((label, row))
            return row

        r = one(WRONG, "答案错", io_mode="stdin")
        ok_all &= r["status"] == ST_WA and r["score"] == 0
        r = one(TLE, "死循环 → 超时")
        ok_all &= r["status"] == ST_TLE
        r = one(CE, "编译不过")
        ok_all &= r["status"] == ST_CE and bool(r.get("compilerTexts"))
        r = one(AC_STDIN, "不用 freopen，严格文件口径 → 应当 0 分", io_mode="file")
        ok_all &= r["status"] != ST_AC and r["score"] == 0
        r = one(AC_FREOPEN, "自己 freopen，严格文件口径 → 满分", io_mode="file")
        ok_all &= r["status"] == ST_AC and r["score"] == 100
        r = one(AC_FREOPEN, "自己 freopen，标准输入输出口径 → 不能通过",
                io_mode="stdin")
        ok_all &= r["status"] != ST_AC
        r = one(AC_STDIN, "标准输入输出（stdin 口径）", io_mode="stdin")
        ok_all &= r["status"] == ST_AC and r["score"] == 100
    finally:
        import shutil
        shutil.rmtree(os.path.join(PROBLEMS_DIR, pid), ignore_errors=True)
    return {"ok": bool(ok_all), "checks": checks}


if __name__ == "__main__":          # 手工自检：python3 -m csp_exam.core.judgelocal
    import sys
    print("题目数据目录：", PROBLEMS_DIR)
    r = selftest()
    for label, row in r["checks"]:
        print("  %-44s -> 状态 %s 得分 %s" % (label, row["status"], row.get("score")))
    print("结论：", "正常" if r["ok"] else "有不对的地方")
    sys.exit(0 if r["ok"] else 1)
