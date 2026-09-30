# -*- coding: utf-8 -*-
"""把 data/ 迁到新结构（v2）：比赛字段、考号格式、题目编号。

**幂等**：重复执行不会重复改（已经迁过的会被跳过）。

用法（在部署根目录下）::

    python3 csp_exam/tools/migrate_v2.py --dry-run   # 只看要改什么，不写盘
    python3 csp_exam/tools/migrate_v2.py             # 真跑

迁移内容：

1. **比赛字段**：`contests.json` 每场补 `level` / `duration_min` / `feedback_mode`
   （缺省 S / 240 / full）
2. **考号格式**：老的 `GD-0001` → 新的 `GD-S10029`（纯随机 5 位）
   - 同步改 `results.json` 的 key
   - 同步改 `uploads/<考号>/` 目录名
3. **题目编号 / 英文名**（两个概念，别混）：给每道题分配 `T00001` 形式的**题目编号**
   （建题时分配、跟着题走、老师不能改，只用来定位查找），把比赛题目里原来的
   `code`（那时存的是**英文名**）搬到 `name`、`code` 换成新的题目编号：
   - `data/problem_codes.json`：每条记录的 `code` 改写成 `T` + 5 位序号
     （原来已是 `T<数字>` 的按序号排前面，其余按题库标识排后面）
   - 每场比赛的 `exam.json`：`problems[].name` = 原来的英文名（没有就取题库里的默认英文名），
     `problems[].code` = 新的题目编号
   - 之后 `store.number_of(prob)` 读题目编号（老师侧）、`store.code_of(prob)` 读英文名（学生侧）
4. 老的 `results.json` 没有 `testcases`（逐点明细）——**不补**，新判分才会有

跑之前请先备份：``tar -czf data-backup-$(date +%F-%H%M).tgz -C <部署根> data``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

# 允许直接 `python3 csp_exam/tools/migrate_v2.py` 跑
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from csp_exam.core import problems, store      # noqa: E402
from csp_exam.config import DATA_DIR           # noqa: E402

#: 新的题目编号格式：T + 5 位数字（老数据里的 `T1`、`T01` 也认，前导零随意）
_NUMBER_RE = re.compile(r"^[Tt]0*(\d+)$")


class Mig:
    def __init__(self, dry: bool):
        self.dry = dry
        self.changes: list[str] = []
        self.errors: list[str] = []

    def say(self, msg: str) -> None:
        self.changes.append(msg)
        print(("  [预演] " if self.dry else "  ") + msg)

    def err(self, msg: str) -> None:
        self.errors.append(msg)
        print("  !! " + msg)

    # ---------------------------------------------------------------- 比赛字段
    def contest_fields(self) -> None:
        items = store.list_contests()
        dirty = False
        for c in items:
            need = {}
            if not c.get("level"):
                need["level"] = store.DEFAULT_LEVEL
            if not c.get("duration_min"):
                need["duration_min"] = store.LEVEL_MINUTES[store.level_of(c)]
            # 老数据里可能还留着 `feedback_mode`：**不补、也不删**。
            # 那个概念已经删掉了（所有比赛一律赛中零反馈），新代码根本不读这个字段。
            if need:
                dirty = True
                self.say(f"比赛 {c['id']}（{c.get('title','')}）补字段：{need}")
                c.update(need)
        if dirty and not self.dry:
            store.save_contests(items)
        if not dirty:
            print("  比赛字段：已是最新，无需改")

    # ---------------------------------------------------------------- 考号
    def kaohaos(self, cid: str) -> None:
        roster = store.load_roster(cid)
        if not roster:
            return
        contest = store.get_contest(cid) or {}
        level = store.level_of(contest)
        prefix = contest.get("prefix") or store.DEFAULT_PREFIX

        # 找出老格式的考号
        old_keys = [k for k in roster if not store.split_kaohao(k)[1]]
        if not old_keys:
            return

        # 本场已占用的号码（新格式的），避免撞车
        taken = set()
        for k in roster:
            _, _, digits = store.split_kaohao(k)
            if digits.isdigit():
                taken.add(int(digits))

        mapping: dict[str, str] = {}
        for old in old_keys:
            nums = store._random_numbers(1, taken)
            mapping[old] = store.make_kaohao(nums[0], prefix, level)

        self.say(f"比赛 {cid}：{len(mapping)} 个考号换新格式，例如 "
                 f"{list(mapping.items())[0][0]} → {list(mapping.items())[0][1]}")

        if self.dry:
            return

        # 1) roster 的 key
        new_roster = {}
        for k, v in roster.items():
            new_roster[mapping.get(k, k)] = v
        store.save_roster(cid, {k: new_roster[k] for k in sorted(new_roster)})

        # 2) results.json 的 key
        results = store.load_results(cid)
        if results:
            new_results = {}
            for k, v in results.items():
                new_results[mapping.get(k, k)] = v
            store.save_results(cid, {k: new_results[k] for k in sorted(new_results)})

        # 3) uploads/<考号> 目录名
        up_root = os.path.join(DATA_DIR, "contests", cid, "uploads")
        if os.path.isdir(up_root):
            for old, new in mapping.items():
                src = os.path.join(up_root, old)
                dst = os.path.join(up_root, new)
                if os.path.isdir(src) and not os.path.exists(dst):
                    try:
                        os.rename(src, dst)
                    except OSError as e:
                        self.err(f"改名失败 {src} → {dst}：{e}")

    # ---------------------------------------------------------------- 题目编号
    def _next_number(self, extra: dict[str, str]) -> str:
        """预演用：算下一个没人用的题目编号（**不写盘**）。

        `extra` 是本次预演里已经算出来的编号（{题库标识: T00001}）。
        """
        used = set()
        for src in (extra or {}, problems.load_codes()):
            for rec in src.values():
                m = _NUMBER_RE.match(str((rec if isinstance(rec, str) else
                                          (rec or {}).get("code") or "")).strip())
                if m:
                    used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return f"T{n:05d}"

    def problem_numbers(self) -> None:
        """把题库编号升级成 `T00001` 格式，并给每场比赛的题目补上编号与英文名。

        老数据里 `problem_codes.json` 的 `code` 存的是**英文名**（`candy`、`m02`）
        或者老的短编号（`T1`）；`exam.json` 的每道题里 `code`/`slug` 也是那个英文名。
        新模型把两个概念拆开：

        * **题目编号** `T00001` —— 老师侧，系统分配、跟着题走、不能改
        * **英文名** `candy` —— 学生侧，学生在比赛页看到的、用来建文件夹/写 freopen 的名字

        所以这里做两件事（**幂等**：已经升级过的记录/题目会被跳过）：

        ① 登记表 `data/problem_codes.json`：`code` 改写成 `T` + 5 位序号。
           顺序按「已经是 T<数字> 的按序号排前、其余按题库标识排后」，
           保证老编号之间的相对顺序不乱；原来的英文名不丢 —— 它已经在
           `exam.json` 的 `slug`/`code` 里（下面 ② 搬到 `name`）。
        ② 每场比赛 `exam.json`：`name` = 原来的英文名（`code`/`slug` 里那个），
           `code` = 题目编号。

        没有出现在任何比赛里的题也会在 ① 里拿到编号（老师按编号找题，
        题库里每道题都该有号）。
        """
        # ---- ① 题库登记表：老编号/英文名 → T00001
        items = problems.load_codes()
        recs = []
        for idx, (pid, rec) in enumerate(items.items()):
            rec = dict(rec or {})
            cur = str(rec.get("code") or "").strip()
            m = _NUMBER_RE.match(cur)
            recs.append((pid, rec, cur, int(m.group(1)) if m else None, idx))
        # 已经是 T<数字> 的排在前面（按序号），其余按原来的顺序（dict 保序 = 登记顺序）
        recs.sort(key=lambda r: (0, r[3], r[4]) if r[3] is not None else (1, 0, r[4]))
        used = {r[3] for r in recs if r[3] is not None}
        nxt = 1
        new_codes: dict[str, str] = {}
        for pid, rec, cur, num, _idx in recs:
            if num is None:
                while nxt in used:
                    nxt += 1
                num = nxt
                used.add(num)
            new_codes[pid] = f"T{num:05d}"
        if not recs:
            print("  题目编号：题库登记表是空的，没有要改的")
        for pid, rec, cur, _num, _idx in recs:
            want = new_codes[pid]
            if cur == want:
                continue
            self.say(f"题库登记 {pid}（{rec.get('title', '')}）编号 {cur or '（没编号）'} → {want}")
            if not self.dry:
                rec["code"] = want
                items[pid] = rec
        if not self.dry and recs:
            problems.save_codes(items)

        # ---- ② 每场比赛：name = 老英文名，code = 题目编号
        backfill: dict[str, str] = {}        # 顺便把「本场在用的英文名」记成题库默认值
        for c in store.list_contests():
            cid = c["id"]
            exam = store.load_exam(cid)
            probs = exam.get("problems") or []
            if not probs:
                continue
            dirty = False
            for p in probs:
                pid = str(p.get("pid") or "")
                number = new_codes.get(pid) or problems.code_of_pid(pid)
                if not number:
                    # 题库登记表里没有这道题（比赛里有、题库里没登记过）→ 补一个
                    if self.dry:
                        # 预演：只算不写（按登记表 + 本次已算出的编号取下一个空号）
                        number = self._next_number(new_codes)
                    else:
                        try:
                            number = problems.assign_number(pid, str(p.get("title") or ""))
                        except problems.CodeError as e:
                            self.err(f"{cid} 的 {pid} 分配题目编号失败：{e}")
                            continue
                    new_codes[pid] = number
                # 已经是新模型的题目（有 name 字段、code 是编号、没有老 slug）→ 一律不碰：
                # 免得把老师定好的名字（或故意留空的名字）又冲掉；只顺手补一下题库默认值。
                if ("name" in p and _NUMBER_RE.match(str(p.get("code") or ""))
                        and "slug" not in p):
                    cur = str(p.get("name") or "").strip()
                    if cur and store.valid_code(cur) and not problems.name_of_pid(pid) \
                            and pid not in backfill:
                        backfill[pid] = cur
                    continue
                # 英文名：老数据里原来的 code/slug 存的就是英文名；已有 name 的沿用。
                # 都取不到就留空 —— 学生侧会退回「按位置」的老兜底 T{题号}（与迁移前学生
                # 看到的一模一样，不会突然改名），老师在本场管理里改一次英文名即可。
                old_name = str(p.get("name") or "").strip()
                legacy = str(p.get("code") or p.get("slug") or "").strip()
                if not old_name and legacy and not _NUMBER_RE.match(legacy):
                    old_name = legacy
                if not old_name or not store.valid_code(old_name):
                    old_name = ""
                if old_name and not problems.name_of_pid(pid) and pid not in backfill:
                    # 题库里还没记过这道题的默认英文名 → 用本场在用的这个补上
                    # （老师下次把这道题加进别的场次时，输入框会自动填它）
                    backfill[pid] = old_name
                self.say(f"比赛 {cid} 第 {p.get('no')} 题（{pid} {p.get('title', '')}）"
                         f"编号 → {number}，英文名 → {old_name or '（待填）'}"
                         f"（原 code={legacy!r}）")
                dirty = True
                if not self.dry:
                    p["name"] = old_name
                    p["code"] = number
                    p.pop("slug", None)      # 老字段：英文名已搬到 name
            if dirty and not self.dry:
                store.save_exam(cid, exam)

        if backfill:
            self.say("题库登记表补上默认英文名：" + "、".join(
                f"{k}→{v}" for k, v in sorted(backfill.items())))
            if not self.dry:
                items = problems.load_codes()
                for pid, nm in backfill.items():
                    rec = dict(items.get(pid) or {})
                    if not str(rec.get("name") or "").strip():
                        rec["name"] = nm
                        items[pid] = rec
                problems.save_codes(items)

    def run(self) -> int:
        print(f"数据目录：{DATA_DIR}")
        print(f"模式：{'预演（不写盘）' if self.dry else '实跑'}\n")

        # 预演要"不写盘"，但 `problems.assign_code()` 内部会把编号**登记**进
        # `data/problem_codes.json`（它没法只算不写）。所以预演前先把登记表存一份，
        # 结束时原样还回去 —— 否则预演会悄悄改掉题库编号（线上踩过）。
        codes_snapshot = None
        codes_file = ""
        if self.dry:
            try:
                codes_file = problems.codes_path()
                if os.path.isfile(codes_file):
                    with open(codes_file, "rb") as f:
                        codes_snapshot = f.read()
                else:
                    codes_snapshot = b""          # 原本就没有，结束时删掉
            except Exception as e:                # 读不到就算了，但要说一声
                self.err(f"预演前无法备份编号登记表，预演可能改动数据：{e}")

        try:
            print("① 比赛字段")
            self.contest_fields()

            print("\n② 考号格式")
            n_before = len(self.changes)
            for c in store.list_contests():
                self.kaohaos(c["id"])
            if len(self.changes) == n_before:
                print("  考号：已全是新格式，无需改")

            print("\n③ 题目编号 / 英文名")
            n2 = len(self.changes)
            self.problem_numbers()
            if len(self.changes) == n2:
                print("  题目编号与英文名：已是最新，无需改")
        finally:
            if self.dry and codes_file:
                try:
                    if codes_snapshot:
                        with open(codes_file, "wb") as f:
                            f.write(codes_snapshot)
                    elif os.path.isfile(codes_file):
                        os.remove(codes_file)
                    print("\n（预演已把编号登记表还原）")
                except Exception as e:
                    self.err(f"还原编号登记表失败，请手工检查 {codes_file}：{e}")

        print("\n" + "=" * 64)
        print(f"共 {len(self.changes)} 处改动，{len(self.errors)} 个错误")
        if self.dry and self.changes:
            print("这是预演，没有写盘。确认无误后去掉 --dry-run 再跑一次。")
        return 1 if self.errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="data/ 迁到 v2 结构")
    ap.add_argument("--dry-run", action="store_true", help="只打印要改什么，不写盘")
    args = ap.parse_args()

    t0 = time.time()
    rc = Mig(args.dry_run).run()
    print(f"耗时 {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
