#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重测：拿学生已经交上来的文件，用**新的本地判题器**再判一遍，并把差异打出来。

为什么需要它：判题从 Hydro 换成 go-judge（`core/gojudge.py` + `core/judgelocal.py`）
之后，判分口径是我们自己定的了 —— 尤其文件输入输出（CSP 的 freopen）这条，
旧系统是"给学生的代码包一层垫片"，新引擎是"原样编译、按 io_mode 决定算哪份输出"。
同一份代码两边可能判出不同分数，所以要能对老提交重跑一遍、并且**看得见差在哪**。

用法（服务器上）：

    # 1) 先看会重测谁、差在哪（不写盘）
    sudo env PYTHONPATH=/root/csp-exam python3 tests/_rejudge.py --today
    # 2) 确认没问题再写回成绩
    sudo env PYTHONPATH=/root/csp-exam python3 tests/_rejudge.py --today --apply

筛选用 `--today`（今天交的）/ `--cid c7` / `--kaohao GD-J02153` / `--all`；
`--limit N` 只跑前 N 条（想先试水的时候用）。

**只写 results.json 里的成绩字段**：`tries`（提交次数）不动 —— 重测不是"又交了一次"；
`submitted_at`（交卷时间）也不动 —— 那是学生交卷的时刻。重测痕迹记在
`rejudged_at` / `rejudge_from` 里，管理端能看到"这道题被重测过、旧分是多少"。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csp_exam.core import grading, hydro_client as hydro, localoj, store, wrapper  # noqa: E402
from csp_exam.core.util import log           # noqa: E402


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def collect(args) -> list[dict]:
    """找出要重测的（比赛, 考号, 题目）三元组。"""
    out = []
    for c in store.list_contests():
        cid = c["id"]
        if args.cid and cid != args.cid:
            continue
        exam = store.load_exam(cid)
        rule = store.rule_of(c)
        probs = {int(p["no"]): p for p in exam.get("problems", [])}
        for kaohao, entry in (store.load_results(cid) or {}).items():
            if args.kaohao and kaohao != args.kaohao:
                continue
            at = str(entry.get("submitted_at") or "")
            if args.today and not at.startswith(_today()):
                continue
            picked = entry.get("picked") or entry.get("structure_picked") or {}
            if not picked and not (entry.get("problems") or {}):
                continue
            base = store.upload_dir(cid, kaohao)
            for no, prob in sorted(probs.items()):
                key = store.problem_dir_name(no)
                got = (entry.get("problems") or {}).get(key) or {}
                rel = picked.get(str(no)) or picked.get(no) or got.get("file") or ""
                if not rel:
                    continue
                src = os.path.join(base, rel)
                if not os.path.isfile(src):
                    continue
                out.append({"cid": cid, "title": c["title"], "kaohao": kaohao,
                            "name": (store.load_roster(cid).get(kaohao) or {}).get("name", "?"),
                            "no": no, "key": key, "pid": str(prob.get("pid") or ""),
                            "code": store.code_of(prob), "full": int(prob.get("full") or 0),
                            "rule": rule, "rel": rel, "src": src,
                            "at": at, "old": got})
    return out


def rejudge_one(item: dict) -> dict:
    """判一份，返回（新行, 备注）。"""
    with open(item["src"], "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    ext = os.path.splitext(item["rel"])[1]
    io_mode = "auto" if item["rule"].get("freopen") else "stdin"
    pid = item["pid"]
    if not pid:
        return {"status": 8, "score": 0, "note": "本场这道题没有题库标识", "testcases": []}, ""
    row = localoj.judge(pid, source, ext, code=item["code"], full=item["full"],
                        io_mode=io_mode)
    # 提示：这份代码有没有自己 freopen（老师最关心这一类差异）
    hint = ""
    if item["rule"].get("freopen"):
        h = wrapper.check_freopen(source, item["code"])
        hint = h or "学生自己写了 freopen"
    return row, hint


def write_back(item: dict, row: dict, hint: str) -> dict:
    """把重测结果写回 results.json（**不动 tries / submitted_at**）。"""
    cid, kaohao, key = item["cid"], item["kaohao"], item["key"]
    entry = (store.load_results(cid) or {}).get(kaohao, {})
    entry.setdefault("problems", {})
    old = dict(entry["problems"].get(key) or {})
    new = {
        "score": int(row.get("score") or 0),
        "status": int(row.get("status", 8)),
        "status_text": hydro.status_text(row.get("status")),
        "time": row.get("time"),
        "memory": row.get("memory"),
        "testcases": grading._pack_cases(cid, kaohao, item["code"],
                                         row.get("testcases"), item["full"]),
        "file": item["rel"],
        "hint": hint,
        "tries": int(old.get("tries", 0)),        # 重测不是新的一次提交
        "rejudged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rejudge_from": {"score": int(old.get("score", 0)),
                         "status_text": str(old.get("status_text") or ""),
                         "engine": "hydro"},
        "note": row.get("note", ""),
    }
    if row.get("compilerTexts"):
        new["compile_error"] = "\n".join(str(x) for x in row["compilerTexts"])[:4000]
    if old.get("prev_score") is not None:
        new["prev_score"] = old["prev_score"]
    entry["problems"][key] = new
    entry["total"] = sum(int(v.get("score") or 0) for v in entry["problems"].values())
    entry["judging"] = False
    store.put_result(cid, kaohao, entry)
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", action="store_true", help="只重测今天交的")
    ap.add_argument("--all", action="store_true", help="所有历史提交")
    ap.add_argument("--cid", default="", help="只重测这一场")
    ap.add_argument("--kaohao", default="", help="只重测这个考号")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（试水）")
    ap.add_argument("--apply", action="store_true", help="真写回（默认只看不写）")
    args = ap.parse_args()
    if not (args.today or args.all or args.cid or args.kaohao):
        ap.error("至少给一个范围：--today / --all / --cid X / --kaohao X")

    health = localoj.health()
    print("判题后端：%s（go-judge %s）" % ("正常" if health["judge_ok"] else "不可用",
                                          health["judge_version"]))
    if health["judge_error"]:
        print("  沙箱问题：", health["judge_error"])
    if not health["judge_ok"]:
        return 1

    items = collect(args)
    if args.limit:
        items = items[:args.limit]
    print("要重测 %d 份（%s）\n" % (len(items), "会写回成绩" if args.apply else "只看不写"))

    changed = same = failed = 0
    for i, it in enumerate(items, 1):
        try:
            row, hint = rejudge_one(it)
        except Exception as e:                                # noqa: BLE001
            print("  [%d/%d] %s %s 第%d题 判题异常：%r"
                  % (i, len(items), it["cid"], it["kaohao"], it["no"], e))
            failed += 1
            continue
        old_score = int(it["old"].get("score") or 0)
        new_score = int(row.get("score") or 0)
        diff = new_score - old_score
        mark = "  " if diff == 0 else ("↑" if diff > 0 else "↓")
        if diff == 0:
            same += 1
        else:
            changed += 1
        print("  [%d/%d] %-6s %-10s %-8s 第%d题 旧 %3d → 新 %3d %s %s"
              % (i, len(items), it["cid"], it["kaohao"], it["name"], it["no"],
                 old_score, new_score, mark, ("（%s）" % hint) if hint and diff else ""))
        if row.get("note"):
            print("        原因：%s" % str(row["note"])[:120])
        if args.apply:
            write_back(it, row, hint)

    print()
    print("合计 %d 份：分数变了 %d 份、没变 %d 份、判题失败 %d 份"
          % (len(items), changed, same, failed))
    if not args.apply and changed:
        print("（这是预演：没有写回。确认没问题就加 --apply）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
