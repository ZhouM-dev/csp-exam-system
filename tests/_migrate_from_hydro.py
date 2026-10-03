#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把题目数据从 Hydro 迁到本地（`data/problems/<pid>/testdata/`），题面落到 `data/statements/`。

**只读 Hydro、只写本地**：Hydro 那边一个字节都不动（回退时它还是完整的）。
迁的是：测试点 `.in/.out`、题面。时限/内存/点数本来就在 `data/problem_info.json`。

跑法（服务器上）：
    sudo env PYTHONPATH=/root/csp-exam python3 /root/csp-exam/tests/_migrate_from_hydro.py [--check]

`--check`：只报"本地齐不齐"，不写盘（切完判题器之后体检用）。
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csp_exam.core import hydro_client as hydro      # noqa: E402
from csp_exam.core import judgelocal, problems as make_problem, store   # noqa: E402

CHECK_ONLY = "--check" in sys.argv


def main() -> int:
    t0 = time.time()
    try:
        hyd = hydro.list_problems()
    except hydro.HydroError as e:
        print("读不到评测站题目列表：%s" % e)
        return 1
    pids = [str(p.get("pid") or "") for p in hyd if p.get("pid")]
    print("评测站上 %d 道题；%s\n" % (len(pids), "只做体检（不写盘）" if CHECK_ONLY else "开始迁移"))

    codes = make_problem.load_codes()
    info = make_problem.load_problem_info()
    stat = {"ok": 0, "nocase": 0, "nostmt": 0, "skip": 0, "bytes": 0}
    rows = []
    for pid in pids:
        dst = judgelocal.cases_dir(pid)
        files = {} if CHECK_ONLY and os.path.isdir(dst) else hydro.problem_data_files(pid)
        n_in = 0
        n_out = 0
        if files and not CHECK_ONLY:
            os.makedirs(dst, exist_ok=True)
            for name, src in files.items():
                base = os.path.basename(name)
                if not base.endswith((".in", ".out")):
                    continue
                try:
                    shutil.copyfile(src, os.path.join(dst, base))
                    stat["bytes"] += os.path.getsize(src)
                except OSError as e:
                    print("  [警告] %s/%s 复制失败：%s" % (pid, base, e))
        # 数一遍本地（不管是刚迁的还是本来就在的）
        cs = judgelocal.cases_of(pid)
        n_in = len(cs)
        n_out = sum(1 for c in cs if c["out"] is not None)
        # 题面：本地没有就从评测站取一份存下来
        spath = os.path.join(store.DATA_DIR, "statements", "%s.md" % pid)
        have_stmt = os.path.isfile(spath) and os.path.getsize(spath) > 0
        if not have_stmt and not CHECK_ONLY:
            try:
                text = hydro.problem_statement(pid, cache_dir="", ttl=0)
            except hydro.HydroError:
                text = ""
            if text.strip():
                os.makedirs(os.path.dirname(spath), exist_ok=True)
                with open(spath, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                have_stmt = True
        tag = ("无数据" if not n_in else
               ("缺答案×%d" % (n_in - n_out) if n_out < n_in else "齐"))
        if not n_in:
            stat["nocase"] += 1
        elif n_out < n_in:
            stat["skip"] += 1
        else:
            stat["ok"] += 1
        if not have_stmt:
            stat["nostmt"] += 1
        rows.append((pid, n_in, n_out, tag, "题面✓" if have_stmt else "题面×",
                     (codes.get(pid) or {}).get("code") or "", (info.get(pid) or {}).get("title") or ""))

    print("%-12s %5s %5s  %-10s %-6s %-8s %s" % ("题号(pid)", "点", "有答案", "状态", "题面", "题目编号", "标题"))
    for pid, a, b, tag, st, code, title in rows:
        print("%-12s %5d %5d  %-10s %-6s %-8s %s" % (pid, a, b, tag, st, code, title[:24]))

    print()
    print("数据齐的 %d 道；无数据的 %d 道；缺答案的 %d 道；缺题面的 %d 道"
          % (stat["ok"], stat["nocase"], stat["skip"], stat["nostmt"]))
    if not CHECK_ONLY:
        print("复制了 %.1f MB；耗时 %.1f 秒" % (stat["bytes"] / 1048576, time.time() - t0))
    print("题目数据目录：%s" % judgelocal.PROBLEMS_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
