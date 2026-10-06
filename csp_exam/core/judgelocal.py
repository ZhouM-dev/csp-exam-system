#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地判题引擎：编译 + 逐点跑 + 比对，产出与原来 Hydro 记录**同形状**的结果。

和 Hydro 的关系：以前编译、跑数据、比对都交给 Hydro，我们只读它的记录；
现在这三件事由这里做（沙箱是 `gojudge.py` 那套），好处是**判分口径完全由我们定**，
尤其是文件输入输出（CSP 的 freopen）这条 —— 以前靠"给学生的代码包一层 freopen 垫片"
去迁就评测机只喂 stdin，包与不包结果就不一样（线上踩过，老师也反馈过）。

题目数据从这一版起放在本地：`data/problems/<pid>/testdata/*.in|*.out`
（从 Hydro 迁出来，见 `tests/_migrate_from_hydro.py`；题面在 `data/statements/<pid>.md`）。

三种输入输出口径（`io_mode`）：

* `auto`   —— 仅供兼容测试，文件输出优先，再看标准输出；正式 CSP 不使用。
* `file`   —— 严格 CSP 口径：答案必须在 `<英文名>.out` 里，光往标准输出打印不算。
  想"按真实考场判"就用它（旧垫片做不到这一点）。
* `stdin`  —— 不往沙箱里放输入文件，只喂标准输入（OI / IOI 赛制：程序本来就该读写标准输入输出）。
"""

from __future__ import annotations

import hashlib
import os
import re
import time

from . import gojudge
from .util import log

#: 题目数据目录（本地题目库）
PROBLEMS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "..", "data", "problems")
PROBLEMS_DIR = os.path.normpath(PROBLEMS_DIR)

#: 沙箱里干活的地方（挂载配置里的 tmpfs）
WORK = "/w"

#: go-judge 的状态串 → 考试服务沿用的状态码（见 judge_result.STATUS）
_STATUS_MAP = {
    "Accepted": 1,
    "Time Limit Exceeded": 3,
    "Memory Limit Exceeded": 4,
    "Output Limit Exceeded": 5,
    "Nonzero Exit Status": 6,
    "Signalled": 6,
    "Dangerous Syscall": 6,          # 碰了沙箱不允许的系统调用：算运行错误
    "File Error": 8,
    "Internal Error": 8,
}
#: 我们自己的状态码（与 judge_result.STATUS 对齐，页面/成绩表都认这几个数）
ST_AC, ST_WA, ST_TLE, ST_MLE, ST_OLE, ST_RE, ST_CE, ST_SE = 1, 2, 3, 4, 5, 6, 7, 8

#: 编译限额（编译也在沙箱里跑）
COMPILE_CPU_MS = 30000
COMPILE_MEM_MB = 2048

#: 支持的语言 → (编译器, 额外参数)
LANGS = {
    ".cpp": ("/usr/bin/g++", ["-O2", "-std=c++14", "-static"]),
    ".cc": ("/usr/bin/g++", ["-O2", "-std=c++14", "-static"]),
    ".cxx": ("/usr/bin/g++", ["-O2", "-std=c++14", "-static"]),
    ".c++": ("/usr/bin/g++", ["-O2", "-std=c++14", "-static"]),
    ".c": ("/usr/bin/gcc", ["-O2", "-std=c11", "-static"]),
}


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
        sp = os.path.join(_store.DATA_DIR, "statements", f"{pid}.md")
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(statement)
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
    sp = os.path.join(_store.DATA_DIR, "statements", f"{pid}.md")
    if os.path.isfile(sp):
        try:
            os.remove(sp)
            gone.append("题面")
        except OSError:
            pass
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


# ------------------------------------------------------------------ 比对

def norm_output(text):
    """把输出归一成"按 CSP 口径比较"的样子（题目须知 §8.2/§8.3）。

    逐行去掉**行末**空白；去掉文尾的空行；行首空白**不动**（行首多空格要算错）。
    行末空格与文件末尾回车的有无不影响正确性 —— 这条是考区明文写的，
    所以归一之后再全文比较。
    """
    if isinstance(text, bytes):
        lines = [ln.rstrip(b" ") for ln in text.replace(b"\r\n", b"\n").split(b"\n")]
        while lines and not lines[-1]:
            lines.pop()
        return b"\n".join(lines)
    lines = [ln.rstrip(" ") for ln in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def compare(got, want) -> bool:
    """答案比对（CSP 口径）。"""
    if isinstance(got, bytes) or isinstance(want, bytes):
        got = got.encode("utf-8") if isinstance(got, str) else got
        want = want.encode("utf-8") if isinstance(want, str) else want
    return norm_output(got) == norm_output(want)


def _decode(b: bytes) -> str:
    return (b or b"").decode("utf-8", "replace")


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
        time / memory   最慢点毫秒 / 峰值 KB（与 Hydro 口径一致：毫秒、KB）
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
                "note": f"暂不支持 {ext or '这种'} 语言（本机判题器目前支持 C/C++）",
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
        row = _judge_inner(pid, source, ext, cases, per, code=code, time_ms=effective_ms,
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


def _judge_inner(pid, source, ext, cases, per, *, code, time_ms, memory_mb, io_mode) -> dict:
    cc, flags = LANGS[ext]
    # ---- 编译（一次；产物留在沙箱仓库里，后面每个点直接引用）
    if not code or not re.fullmatch(r"[a-z0-9_]{1,32}", code):
        raise gojudge.GoJudgeError("题目英文名不安全")
    src_name = code + ext
    src_id = gojudge.prepare(source.encode("utf-8") if isinstance(source, str) else source)
    try:
        r = gojudge.run([cc, *flags, src_name, "-o", code],
                        copy_in={src_name: {"fileId": src_id}},
                        cpu_ms=COMPILE_CPU_MS, clock_ms=COMPILE_CPU_MS + 20000,
                        memory_mb=COMPILE_MEM_MB, stack_mb=COMPILE_MEM_MB, cached=[code])
    finally:
        gojudge.drop(src_id)
    cerr = (r.get("files") or {}).get("stderr", b"")
    if r.get("status") != "Accepted" or code not in (r.get("fileIds") or {}):
        for fid in (r.get("fileIds") or {}).values():
            gojudge.drop(fid)
        return {"status": ST_CE, "score": 0,
                "note": "编译错误" if r.get("status") == "Accepted" else
                        f"编译失败（{r.get('status')}）",
                "compilerTexts": [_decode(cerr)[:4000]],
                "testcases": []}
    binary = r["fileIds"][code]

    # ---- 逐点跑
    rows, slowest, peak = [], 0, 0
    try:
      for i, c in enumerate(cases):
        got, st, t_ms, mem_kb, extra = _run_case(binary, c, code=code, time_ms=time_ms,
                                                 memory_mb=memory_mb, io_mode=io_mode)
        ans = _decode(c["out"]) if c["out"] is not None else ""
        if st == ST_AC and c["out"] is not None and not compare(got, c["out"]):
            st = ST_WA
        if c["out"] is None and st == ST_AC:
            st = ST_WA               # 没有答案的点：对任何输出都算错（与旧系统一致）
        rows.append({
            "no": c["name"], "status": st, "score": int(per[i]) if st == ST_AC else 0,
            "time": t_ms, "memory": mem_kb,
            "input": _decode(c["in"])[:4096],
            "output": (_decode(got) if isinstance(got, bytes) else got)[:4096],
            "answer": ans[:4096],
            **extra,
        })
        slowest = max(slowest, t_ms)
        peak = max(peak, mem_kb)
    finally:
        gojudge.drop(binary)

    passed = sum(1 for x in rows if x["status"] == ST_AC)
    score = sum(int(x["score"]) for x in rows)
    # 整题状态：全过=通过；有点过=部分正确（沿用旧系统的"按点给分"显示）；
    # 一个没过时，用**第一个失败点的状态**（学生/老师看的是"为什么没过"）
    if any(x["status"] == ST_SE for x in rows):
        status = ST_SE
    elif passed == len(rows):
        status = ST_AC
    else:
        status = next((x["status"] for x in rows if x["status"] != ST_AC), ST_WA)
    return {"status": status, "score": score, "testcases": rows,
            "time": slowest, "memory": peak,
            "note": "测试点执行发生系统错误，需重测" if status == ST_SE else "",
            "passed": passed, "total": len(rows)}


def _run_case(binary: str, case: dict, *, code: str, time_ms: int, memory_mb: int,
              io_mode: str) -> tuple[bytes, int, int, int, dict]:
    """直接运行选手程序；答案原始字节取回，CSP 不给标准输入兜底。"""
    from . import store
    if not store.valid_code(code) or io_mode not in ("file", "stdin", "auto"):
        raise gojudge.GoJudgeError("不安全的题目英文名或 I/O 配置")
    infile, outfile = code + ".in", code + ".out"
    input_id = gojudge.prepare(case["in"])
    cached_ids = []
    try:
        copy_in = {code: {"fileId": binary}}
        if io_mode != "stdin":
            copy_in[infile] = {"fileId": input_id}
        stdin = {"content": ""} if io_mode == "file" else {"fileId": input_id}
        r = gojudge.run(["./" + code], copy_in=copy_in,
                        files=[stdin, {"name": "stdout", "max": 64 * 1024 * 1024},
                               {"name": "stderr", "max": 1024 * 1024}],
                        cpu_ms=int(time_ms), clock_ms=int(time_ms) * 3 + 3000,
                        memory_mb=int(memory_mb), stack_mb=int(memory_mb),
                        proc_limit=1, copy_out=["stderr"],
                        cached=["stdout", outfile + "?"] if io_mode != "stdin" else ["stdout"])
        ids = r.get("fileIds") or {}
        cached_ids.extend(ids.values())
        st = _STATUS_MAP.get(str(r.get("status")), ST_SE)
        if any(e.get("type") in ("CopyOutSizeExceeded", "CollectSizeExceeded")
               for e in r.get("fileError", [])):
            st = ST_OLE
        if st == ST_AC and int(r.get("time") or 0) > int(time_ms) * 10**6:
            st = ST_TLE
        if st == ST_AC and int(r.get("memory") or 0) > int(memory_mb) * 1024 * 1024:
            st = ST_MLE
        extra = {}
        chosen = outfile if io_mode != "stdin" and outfile in ids else "stdout"
        if io_mode == "file" and outfile not in ids and st == ST_AC:
            st = ST_WA
            extra["message"] = f"没有生成 {outfile}，CSP 要求使用文件输入输出"
        got = gojudge.fetch(ids[chosen]) if chosen in ids else b""
        err = _decode((r.get("files") or {}).get("stderr", b""))
        if err:
            extra.setdefault("message", err[:500])
        return (got, st, int(round(int(r.get("time") or 0) / 1e6)),
                int(round(int(r.get("memory") or 0) / 1024)), extra)
    finally:
        gojudge.drop(input_id)
        for fid in cached_ids:
            gojudge.drop(fid)


def selftest() -> dict:
    """自检：引擎本身对不对（不需要题目数据，现造两个点）。

    要盖住：AC、WA、TLE、编译错误，以及** freopen 与不用 freopen 两种写法在
    auto/file 两种口径下的差别**——这正是老师反馈"结果不一样"的地方。
    """
    import tempfile
    ok_all = True
    checks = []
    pid = "_selftest"
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
        TLE = ('#include <cstdio>\nint main(){for(;;);return 0;}\n')
        CE = 'int main(){this is not c++}\n'

        def one(src, label, **kw):
            nonlocal ok_all
            row = judge_source(pid, src, ".cpp", code="candy", full=100,
                               time_ms=1000, memory_mb=256, **kw)
            checks.append((label, row))
            return row

        r = one(AC_STDIN, "标准输入输出（auto）", io_mode="auto")
        ok_all &= r["status"] == ST_AC and r["score"] == 100
        r = one(AC_FREOPEN, "自己 freopen（auto）", io_mode="auto")
        ok_all &= r["status"] == ST_AC and r["score"] == 100
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
        r = one(AC_FREOPEN, "自己 freopen，标准输入输出口径 → 读不到文件（超时或运行错误）",
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
