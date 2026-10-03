#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地题目库：换掉 Hydro 之后，题目数据、题面、题库列表都归这里。

为什么单独一个模块：判题换成 go-judge 之后，"题目"这件事就全在本地了 ——
`data/problems/<pid>/testdata/*.in|*.out` 是数据，`data/statements/<pid>.md` 是题面，
登记表还是 `problem_codes.json`（题目编号/默认英文名）与 `problem_info.json`（时限/内存/删没删）。

这个模块的**函数签名刻意和 `hydro_client` 里那几个对齐**（`list_problems` /
`problem_statement` / `read_problem_case`），这样页面那边只要换个模块名，
不用改调用姿势 —— 迁移期间少动一处就少一处风险。
"""

from __future__ import annotations

import os

from . import judgelocal, problems as make_problem, store


def problem_pids() -> set:
    """本机有哪些题（判"标识被占用"看这里；评测站已经不管事了）。"""
    return judgelocal.known_pids()


def list_problems() -> list:
    """题库清单：`[{"pid", "title"}]`，按题目编号排序（没有编号的排最后）。

    标题取建题时记下的那个（`problem_info.json`）；没记的用标识兜底 ——
    以前这个列表是从评测站拉的，现在本地就是权威。
    """
    codes = make_problem.load_codes()
    info = make_problem.load_problem_info()
    rows = []
    for pid in judgelocal.known_pids():
        rec = info.get(pid) or {}
        code = str(rec.get("code") or (codes.get(pid) or {}).get("code") or "")
        rows.append({"pid": pid, "title": str(rec.get("title") or pid), "code": code})
    rows.sort(key=lambda r: (r["code"] == "", r["code"] or r["pid"]))
    return rows


def problem_statement(pid: str, cache_dir: str = "", ttl: int = 0) -> str:
    """题面（Markdown 原文）—— **本地文件就是正本**：`data/statements/<pid>.md`。

    后两个参数是为了和 `hydro_client.problem_statement(pid, cache_dir, ttl)` 的
    调用姿势兼容而留的，本地实现用不上（没有"从别处取回来缓存"这回事了）。
    """
    pid = str(pid or "").strip()
    if not pid:
        return ""
    path = os.path.join(store.DATA_DIR, "statements", f"{pid}.md")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def set_problem_statement(pid: str, text: str) -> dict:
    """改题面：直接写本地文件（不用再"两处都写"了 —— 只有这一处）。

    返回 `{"ok", "field", "error"}`，与旧的 `hydro_client.set_problem_statement` 同形，
    调用点（`_admin_statement_post`）不用改逻辑。
    """
    pid = str(pid or "").strip()
    if not pid:
        return {"ok": False, "error": "没有指定题目标识"}
    if pid not in problem_pids():
        return {"ok": False, "error": f"本机题目库里没有 {pid}"}
    path = os.path.join(store.DATA_DIR, "statements", f"{pid}.md")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write((text or "").replace("\r\n", "\n").replace("\r", "\n"))
    except OSError as e:
        return {"ok": False, "error": f"写题面失败：{e}"}
    return {"ok": True, "field": "file", "modified": 1}


def read_problem_case(pid: str, case_id) -> tuple:
    """读某个测试点的 (输入, 标准答案) —— 直接读本地数据文件。

    `case_id` 是逐点记录里的 `id`/`no`（从 1 开始或就是文件名的前半段），
    与 `testdata/<名>.in|.out` 对应（**只认文件名，不认顺序** —— 名字是 `1`、`01`、
    `sub1_1` 都行，取不到就返回空串）。
    """
    pid = str(pid or "").strip()
    key = str(case_id or "").strip()
    if not pid or not key:
        return "", ""
    d = judgelocal.cases_dir(pid)
    cand = [key]
    if key.isdigit():                      # 记录里给的是序号：补前导零的写法也试一下
        cand += [f"{int(key):02d}", key.lstrip("0") or "0"]
    for name in cand:
        ip = os.path.join(d, f"{name}.in")
        if not os.path.isfile(ip):
            continue
        def _read(p):
            try:
                with open(p, "rb") as f:
                    return f.read().decode("utf-8", "replace")
            except OSError:
                return ""
        return _read(ip), _read(os.path.join(d, f"{name}.out"))
    # 兜底：按顺序数（第 N 个文件）
    cases = judgelocal.cases_of(pid)
    if key.isdigit() and 1 <= int(key) <= len(cases):
        c = cases[int(key) - 1]
        return ((c["in"] or b"").decode("utf-8", "replace"),
                (c["out"] or b"").decode("utf-8", "replace") if c["out"] is not None else "")
    return "", ""


def judge(pid: str, source: str, ext: str, *, code: str = "", full: int = 100,
          io_mode: str = "auto") -> dict:
    """判一份代码（带时限/内存的自动取值）。「自己测试」和管理端都用这个。"""
    t_ms, mem_mb = judgelocal.limits_of(pid)
    return judgelocal.judge_source(pid, source, ext, code=code, full=full,
                                   time_ms=t_ms, memory_mb=mem_mb, io_mode=io_mode)


def health() -> dict:
    """判题后端体检（页面/看板用）：沙箱在不在、题目数据齐不齐。"""
    g = judgelocal.gojudge.probe()
    pids = sorted(problem_pids())
    no_data = [p for p in pids if not judgelocal.cases_of(p)]
    return {"judge_ok": bool(g.get("ok")), "judge_version": g.get("version", ""),
            "judge_error": g.get("error", ""), "problems": len(pids),
            "no_data": no_data}
